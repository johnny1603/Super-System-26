"""Run every tests/test_*.py (stdlib unittest, no installs needed) and exit
non-zero if any file fails. Used by .github/workflows/tests.yml, by the deploy
workflow before it deploys, and locally:

    python -I -B scripts/run_tests.py
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, "tests")


def _data_state() -> dict:
    """Every file under <repo>/data with its size and time — the suite must leave it untouched."""
    out = {}
    for folder, _dirs, names in os.walk(os.path.join(ROOT, "data")):
        for name in names:
            full = os.path.join(folder, name)
            try:
                st = os.stat(full)
                out[os.path.relpath(full, ROOT)] = (st.st_size, st.st_mtime_ns)
            except OSError:
                pass
    return out


def main() -> int:
    # A failing test's Hebrew output must not crash the runner on a non-UTF-8 console
    sys.stdout.reconfigure(errors="replace")
    files = sorted(f for f in os.listdir(TESTS) if f.startswith("test_") and f.endswith(".py"))
    failed = []
    before = _data_state()
    for name in files:
        run = subprocess.run([sys.executable, "-I", "-B", name], cwd=TESTS, capture_output=True,
                             text=True, encoding="utf-8", errors="replace")
        tail = [ln for ln in (run.stderr + run.stdout).splitlines() if ln.startswith(("OK", "FAILED", "Ran "))]
        status = "ok" if run.returncode == 0 else "FAILED"
        print(f"{status:7} {name}  {' '.join(tail[-2:])}")
        if run.returncode != 0:
            failed.append(name)
            print(run.stderr[-4000:])
    print(f"\n{len(files) - len(failed)}/{len(files)} test files passed")
    after = _data_state()
    touched = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    if touched:
        # Tests never write under data/ (core/local_data): this is a failure, not a warning
        print(f"FAILED  the suite wrote under data/: {', '.join(touched)}")
        return 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
