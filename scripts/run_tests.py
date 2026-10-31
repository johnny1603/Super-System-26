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


def main() -> int:
    files = sorted(f for f in os.listdir(TESTS) if f.startswith("test_") and f.endswith(".py"))
    failed = []
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
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
