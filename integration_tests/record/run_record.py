#!/usr/bin/env python3
"""
Run all record_*.py scripts with --base-url and --out recordings/<area>.
Usage: from repo root: python integration_tests/record/run_record.py [--base-url URL]
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_INTEGRATION_TESTS_DIR = os.path.dirname(_SCRIPT_DIR)
_ROOT = os.path.dirname(_INTEGRATION_TESTS_DIR)
_RECORDINGS_BASE = os.path.join(_INTEGRATION_TESTS_DIR, "recordings")


def main() -> int:
    parser = argparse.ArgumentParser(description="Record all areas (reports, sprints, issues, ai_insights, agent_jobs)")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("BACKEND_BASE_URL", "http://localhost:8000"),
        help="Backend base URL",
    )
    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")

    scripts = []
    for name in sorted(os.listdir(_SCRIPT_DIR)):
        if name.startswith("record_") and name.endswith(".py") and name != "run_record.py" and name != "record_common.py":
            area = name.replace("record_", "").replace(".py", "")
            scripts.append((os.path.join(_SCRIPT_DIR, name), area))

    if not scripts:
        print("No record_*.py scripts found")
        return 1

    print(f"Recording {len(scripts)} area(s) at {base_url}")
    failed = []
    for script_path, area in scripts:
        out_dir = os.path.join(_RECORDINGS_BASE, area)
        cmd = [sys.executable, script_path, "--base-url", base_url, "--out", out_dir]
        print(f"\n--- {area} -> {out_dir} ---")
        r = subprocess.run(cmd, cwd=_ROOT)
        if r.returncode != 0:
            failed.append(area)
    if failed:
        print(f"\nFailed: {', '.join(failed)}")
        return 1
    print("\nAll areas recorded.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
