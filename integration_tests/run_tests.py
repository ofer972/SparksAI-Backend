#!/usr/bin/env python3
"""
Integration test runner. Discovers test_*.py under tests/, runs them serial or parallel.
Usage:
  python integration_tests/run_tests.py
  python integration_tests/run_tests.py --base-url http://localhost:8000 --parallel
  python integration_tests/run_tests.py --ignore-replay   # live only; skip replay (recording comparison)
  python -m integration_tests.run_tests --base-url http://localhost:8000
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed


# Ensure repo root is on path so "integration_tests.*" imports work
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_SCRIPT_DIR)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_R = "\033[31m"   # red
_N = "\033[0m"    # reset


def _discover_tests() -> list[tuple[str, str]]:
    """Return list of (module_name, module_path) for tests/test_*.py."""
    tests_dir = os.path.join(_SCRIPT_DIR, "tests")
    if not os.path.isdir(tests_dir):
        return []
    out = []
    for name in sorted(os.listdir(tests_dir)):
        if name.startswith("test_") and name.endswith(".py"):
            mod_name = name[:-3]
            path = os.path.join(tests_dir, name)
            if os.path.isfile(path):
                out.append((mod_name, path))
    return out


def _run_one(module_name: str, module_path: str, base_url: str, ignore_replay: bool = False) -> tuple[str, bool, str, float]:
    """Load test module, call run(base_url, ignore_replay=...), return (name, ok, message, duration)."""
    spec = importlib.util.spec_from_file_location(
        f"integration_tests.tests.{module_name}",
        module_path,
    )
    if spec is None or spec.loader is None:
        return module_name, False, "failed to load module", 0.0
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    run_fn = getattr(mod, "run", None)
    if not callable(run_fn):
        return module_name, False, "module has no run(base_url) function", 0.0
    start = time.perf_counter()
    try:
        import inspect
        if inspect.signature(run_fn).parameters.get("ignore_replay") is not None:
            ok, msg = run_fn(base_url, ignore_replay=ignore_replay)
        else:
            ok, msg = run_fn(base_url)
        duration = time.perf_counter() - start
        return module_name, ok, msg, duration
    except Exception as e:
        duration = time.perf_counter() - start
        return module_name, False, str(e), duration


def main() -> int:
    parser = argparse.ArgumentParser(description="Run integration tests")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("BACKEND_BASE_URL", "http://localhost:8000"),
        help="Backend base URL (default: env BACKEND_BASE_URL or http://localhost:8000)",
    )
    parser.add_argument(
        "--parallel",
        action="store_true",
        help="Run tests in parallel (thread pool)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Max workers when --parallel (default: 4)",
    )
    parser.add_argument(
        "--ignore-replay",
        action="store_true",
        help="Run only live assertions; skip replay (comparison to recordings). Default: live + replay.",
    )
    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")
    ignore_replay = getattr(args, "ignore_replay", False)

    tests = _discover_tests()
    if not tests:
        print("No tests found under integration_tests/tests/ (test_*.py)")
        return 1

    print(f"Backend: {base_url}")
    print(f"Tests: {[t[0] for t in tests]}")
    print(f"Mode: {'parallel' if args.parallel else 'serial'}")
    print(f"Replay: {'ignored (live only)' if ignore_replay else 'on (live + replay)'}")
    print()

    results: list[tuple[str, bool, str, float]] = []
    if args.parallel:
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {
                executor.submit(_run_one, name, path, base_url, ignore_replay): name
                for name, path in tests
            }
            for future in as_completed(futures):
                results.append(future.result())
        # Keep order by test name for stable output
        results.sort(key=lambda x: x[0])
    else:
        for name, path in tests:
            results.append(_run_one(name, path, base_url, ignore_replay))

    passed = sum(1 for _, ok, _, _ in results if ok)
    failed = len(results) - passed
    for name, ok, msg, duration in results:
        status = "PASS" if ok else "FAIL"
        line = f"  [{status}] {name}  ({duration:.2f}s)  {msg}"
        print(f"{_R}{line}{_N}" if not ok else line)
    print()
    summary = f"Passed: {passed}, Failed: {failed}, Total: {len(results)}"
    print(f"{_R}{summary}{_N}" if failed else summary)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
