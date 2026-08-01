"""Section 1 -- acquisition and immutable archiving of NIH ExPORTER files.

Every downloaded file is recorded in data/raw/manifest.json with its source URL,
download timestamp, fiscal year, byte size, SHA-256, and the header fingerprint
of the CSV it contains. Re-running acquisition against an already-archived file
verifies the checksum instead of re-downloading; a changed checksum is a hard
error, not a silent replacement.
"""
from __future__ import annotations

import io
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from .paths import MANIFEST, RAW
from .util import PipelineError, get_logger, read_json, sha256_file, sha256_text, utcnow, write_json

log = get_logger("acquire")


def _download(url: str, dest: Path, user_agent: str, timeout: int, retries: int) -> None:
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": user_agent})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status != 200:
                    raise PipelineError(f"HTTP {resp.status} for {url}")
                tmp = dest.with_suffix(dest.suffix + ".part")
                with tmp.open("wb") as fh:
                    while chunk := resp.read(1 << 20):
                        fh.write(chunk)
                tmp.replace(dest)
            return
        except (urllib.error.URLError, TimeoutError, PipelineError) as exc:
            last = exc
            log.warning("attempt %d/%d failed for %s: %s", attempt, retries, url, exc)
    raise PipelineError(f"could not download {url}: {last}")


def _zip_facts(path: Path) -> dict:
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        if len(names) != 1:
            raise PipelineError(f"{path.name}: expected exactly one member, found {names}")
        member = names[0]
        info = zf.getinfo(member)
        with zf.open(member) as fh:
            head = io.TextIOWrapper(fh, encoding="latin-1").readline().rstrip("\r\n")
    columns = [c.strip('"') for c in head.split('","')]
    columns = [columns[0].lstrip('"'), *columns[1:]]
    if columns:
        columns[-1] = columns[-1].rstrip('"')
    return {
        "member_name": member,
        "member_bytes": info.file_size,
        "member_modified": "%04d-%02d-%02dT%02d:%02d:%02d" % info.date_time,
        "column_count": len(columns),
        "columns": columns,
        "header_sha256": sha256_text(head),
    }


def acquire_year(fy: int, cfg: dict, force: bool = False) -> dict:
    acq = cfg["acquisition"]
    url = acq["projects_url_template"].format(fy=fy)
    dest = RAW / f"RePORTER_PRJ_C_FY{fy}.zip"

    manifest = read_json(MANIFEST) or {"files": {}, "created_at": utcnow()}
    key = dest.name
    prior = manifest["files"].get(key)

    if dest.exists() and not force:
        digest = sha256_file(dest)
        if prior and prior["sha256"] != digest:
            raise PipelineError(
                f"{key}: on-disk checksum {digest[:12]} does not match manifest "
                f"{prior['sha256'][:12]}. The archive has been altered. Refusing to proceed."
            )
        if prior:
            log.info("%s already archived and verified (%s)", key, digest[:12])
            return prior
    else:
        log.info("downloading FY%s from %s", fy, url)
        _download(url, dest, acq["user_agent"], int(acq["timeout_seconds"]), int(acq["retries"]))
        digest = sha256_file(dest)

    record = {
        "file": key,
        "source_url": url,
        "downloaded_at": utcnow(),
        "fiscal_year": fy,
        "file_version": "NIH ExPORTER annual project file (PRJ_C), retrieved as published",
        "bytes": dest.stat().st_size,
        "sha256": digest,
        "data_dictionary": "NIH RePORTER ExPORTER data dictionary, "
        "https://reporter.nih.gov/exporter/documentation (projects, PRJ)",
        **_zip_facts(dest),
    }

    if prior and prior["sha256"] != record["sha256"]:
        record["supersedes_sha256"] = prior["sha256"]
        record["replacement_note"] = (
            "NIH reissued this file. Prior checksum retained; all downstream outputs "
            "must be rebuilt and the change described in the release notes."
        )
        log.warning("FY%s file changed upstream; previous sha256 %s", fy, prior["sha256"][:12])

    manifest["files"][key] = record
    manifest["updated_at"] = utcnow()
    write_json(MANIFEST, manifest)
    log.info("archived %s  %.1f MB  sha256=%s", key, record["bytes"] / 1e6, digest[:12])
    return record


def acquire_all(cfg: dict, force: bool = False) -> dict:
    return {fy: acquire_year(fy, cfg, force=force) for fy in cfg["fiscal_years"]}
