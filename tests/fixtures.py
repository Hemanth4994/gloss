"""Build the synthetic warehouse once per test run and share it between test modules."""
import atexit
import os
import tempfile
from functools import lru_cache

from gloss.pipeline import build_warehouse

_dir = tempfile.TemporaryDirectory()
atexit.register(_dir.cleanup)


@lru_cache(maxsize=1)
def warehouse():
    """Returns (path, clean tables, quality report, raw data)."""
    path = os.path.join(_dir.name, "gloss.duckdb")
    tables, report, raw = build_warehouse(path)
    return path, tables, report, raw
