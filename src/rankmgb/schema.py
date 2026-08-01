"""Section 1 -- schema contract.

The pipeline fails explicitly when expected files, columns, or record counts
change. Required columns are the ones any downstream stage reads; a missing one
aborts the run. Unexpected new columns are logged, not silently dropped.
"""
from __future__ import annotations

from .util import PipelineError, get_logger

log = get_logger("schema")

REQUIRED_COLUMNS: tuple[str, ...] = (
    "APPLICATION_ID",
    "ACTIVITY",
    "ADMINISTERING_IC",
    "APPLICATION_TYPE",
    "BUDGET_START",
    "BUDGET_END",
    "CORE_PROJECT_NUM",
    "FULL_PROJECT_NUM",
    "FUNDING_MECHANISM",
    "FY",
    "IC_NAME",
    "ORG_CITY",
    "ORG_COUNTRY",
    "ORG_DEPT",
    "ORG_DUNS",
    "ORG_IPF_CODE",
    "ORG_NAME",
    "ORG_STATE",
    "PI_IDS",
    "PI_NAMEs",
    "PROJECT_START",
    "PROJECT_END",
    "PROJECT_TITLE",
    "SUBPROJECT_ID",
    "SUPPORT_YEAR",
    "DIRECT_COST_AMT",
    "INDIRECT_COST_AMT",
    "TOTAL_COST",
    "TOTAL_COST_SUB_PROJECT",
)

# Sanity floors. NIH publishes roughly 75k-90k extramural + intramural
# award-years per FY; a file an order of magnitude off is a bad download or a
# truncated reissue, not a real change in NIH funding.
MIN_ROWS_PER_FY = 50_000
MAX_ROWS_PER_FY = 200_000


def validate_columns(fy: int, columns: list[str]) -> None:
    have = set(columns)
    missing = [c for c in REQUIRED_COLUMNS if c not in have]
    if missing:
        raise PipelineError(
            f"FY{fy}: NIH ExPORTER schema changed. Required columns absent: {missing}. "
            f"Update src/rankmgb/schema.py deliberately and re-run; do not coerce."
        )
    extra = sorted(have - set(REQUIRED_COLUMNS))
    if extra:
        log.info("FY%s: %d columns present but unused: %s", fy, len(extra), ", ".join(extra))


def validate_row_count(fy: int, n: int) -> None:
    if not (MIN_ROWS_PER_FY <= n <= MAX_ROWS_PER_FY):
        raise PipelineError(
            f"FY{fy}: {n:,} rows is outside the expected range "
            f"[{MIN_ROWS_PER_FY:,}, {MAX_ROWS_PER_FY:,}]. Refusing to proceed."
        )
