"""Shared helpers: config loading, logging, checksums, provenance stamps."""
from __future__ import annotations

import hashlib
import json
import logging
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import CONFIG, LOGS

_LOG_CONFIGURED = False


def get_logger(name: str) -> logging.Logger:
    global _LOG_CONFIGURED
    if not _LOG_CONFIGURED:
        fmt = "%(asctime)s %(levelname)-7s %(name)-22s %(message)s"
        logging.basicConfig(level=logging.INFO, format=fmt, stream=sys.stdout)
        fh = logging.FileHandler(LOGS / "pipeline.log")
        fh.setFormatter(logging.Formatter(fmt))
        logging.getLogger().addHandler(fh)
        _LOG_CONFIGURED = True
    return logging.getLogger(name)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_config(path: Path = CONFIG) -> dict:
    import yaml

    cfg = yaml.safe_load(path.read_text())
    if not isinstance(cfg, dict):
        raise PipelineError(f"config at {path} did not parse to a mapping")
    return cfg


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def environment_stamp() -> dict:
    try:
        import pandas as pd

        pandas_v = pd.__version__
    except Exception:
        pandas_v = None
    try:
        import numpy as np

        numpy_v = np.__version__
    except Exception:
        numpy_v = None
    try:
        import matplotlib

        mpl_v = matplotlib.__version__
    except Exception:
        mpl_v = None
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10
        ).stdout.strip() or None
    except Exception:
        commit = None
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "pandas": pandas_v,
        "numpy": numpy_v,
        "matplotlib": mpl_v,
        "git_commit": commit,
        "stamped_at": utcnow(),
    }


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text()) if path.exists() else None


class PipelineError(RuntimeError):
    """Raised when the pipeline must fail loudly rather than degrade."""
