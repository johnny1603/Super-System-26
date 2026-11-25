"""The instance's local `data/` folder — and the rule that a TEST never writes
there (2026-11-26).

`data/` holds runtime files of one running instance (the alert history the
monitor scans, its memory, the architect's status files). It is wiped on
every deploy and is not part of the image (.dockerignore) or of git.

The test suite used to write into it: `agents/master_agent.alert()` appends
to data/alert_history.json, and a test that reaches a real alert path left
its "client 999999" / "knowledge/nope.md" lines in the real file — which was
then read as real history. So the folder is resolved HERE, once:

- `UALLAK_DATA_DIR` set  → that folder (an operator's explicit choice);
- a test process         → a temp folder of its own, removed at exit;
- otherwise              → <repo>/data.

A test process = the main script lives in tests/ or is named test_*.py, or
the process is unittest / pytest / scripts/run_tests.py. Production starts
from uvicorn and matches none of these.
"""
import atexit
import os
import shutil
import sys
import tempfile

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL_DIR = os.path.join(BASE_DIR, "data")
ENV = "UALLAK_DATA_DIR"

_temp = None


def under_test() -> bool:
    main = getattr(sys.modules.get("__main__"), "__file__", "") or ""
    path = os.path.abspath(main).replace("\\", "/").lower() if main else ""
    name = os.path.basename(path)
    return bool("pytest" in sys.modules or "/tests/" in path or name.startswith("test_")
                or name == "run_tests.py" or "/unittest/" in path)


def data_dir() -> str:
    """The folder local runtime files go to. Never <repo>/data under a test."""
    global _temp
    chosen = os.environ.get(ENV, "").strip()
    if chosen:
        return chosen
    if under_test():
        if _temp is None:
            _temp = tempfile.mkdtemp(prefix="uallak-test-data-")
            atexit.register(shutil.rmtree, _temp, True)
        return _temp
    return REAL_DIR


def path(name: str) -> str:
    return os.path.join(data_dir(), name)
