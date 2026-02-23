"""
Test report responses: live and/or replay (compare to recordings).
For each recording (report + team, report + group), re-send the request;
default: compare response to recording; with --ignore-replay: live only (assert 200).
"""

from __future__ import annotations

import json
import os
import sys

# Repo root for path to recordings; add to path when run standalone
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_INTEGRATION_TESTS_DIR = os.path.dirname(_SCRIPT_DIR)
_ROOT = os.path.dirname(_INTEGRATION_TESTS_DIR)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from integration_tests.client import BackendClient

_G = "\033[32m"
_R = "\033[31m"
_N = "\033[0m"

RECORDINGS_REPORTS_DIR = os.path.join(_INTEGRATION_TESTS_DIR, "recordings", "reports")

# Exclude DORA and PR/pull-request reports from integration tests
def _excluded_report(base_name: str) -> bool:
    return base_name.startswith("dora-") or base_name.startswith("pr-workflow-")

# Keys to ignore when comparing response bodies (volatile / timestamp / cache metadata)
_IGNORED_BODY_KEYS = frozenset({
    "cached", "cache_ttl", "updated_at", "created_at", "timestamp",
    "generated_at", "last_updated", "recorded_at", "snapshot_date",
    "updated_at_utc", "generated_at",
})


def _get_result_count(body: dict) -> int | None:
    """Extract result count from response body (meta.count, result.count, or result length)."""
    if not isinstance(body, dict):
        return None
    data = body.get("data") or {}
    meta = data.get("meta") or {}
    if isinstance(meta.get("count"), (int, float)):
        return int(meta["count"])
    result = data.get("result")
    if isinstance(result, dict) and "count" in result:
        c = result["count"]
        if isinstance(c, (int, float)):
            return int(c)
    if isinstance(result, list):
        return len(result)
    return None


def _pass(label: str, count: int | None = None) -> None:
    if count is not None:
        print(f"  {_G}PASS{_N} {label} count={count}")
    else:
        print(f"  {_G}PASS{_N} {label}")


def _fail(label: str, msg: str) -> None:
    print(f"  {_R}FAIL{_N} {label}: {msg}")


def _deep_equal(a, b, ignored_keys: frozenset[str] | None = None) -> bool:
    """Deep equality for JSON-like structures; optional keys ignored in dicts."""
    ign = ignored_keys or frozenset()
    if type(a) != type(b):
        return False
    if a is None or isinstance(a, (str, int, float, bool)):
        return a == b
    if isinstance(a, list):
        if len(a) != len(b):
            return False
        return all(_deep_equal(x, y, ign) for x, y in zip(a, b))
    if isinstance(a, dict):
        ka, kb = set(a.keys()), set(b.keys())
        # Compare only keys that are not ignored
        ka_cmp = ka - ign
        kb_cmp = kb - ign
        if ka_cmp != kb_cmp:
            return False
        return all(_deep_equal(a[k], b[k], ign) for k in ka_cmp)
    return a == b


def _first_diff_path(
    a: object, b: object, ignored_keys: frozenset[str], path: str = ""
) -> tuple[str, object, object] | None:
    """Return first path where a and b differ: (path_str, expected_val, actual_val), or None if equal."""
    ign = ignored_keys
    if type(a) != type(b):
        return (path or "(root)", a, b)
    if a is None or isinstance(a, (str, int, float, bool)):
        return None if a == b else (path or "(root)", a, b)
    if isinstance(a, list):
        if len(a) != len(b):
            return (f"{path}[len]" if path else "[len]", len(a), len(b))
        for i, (x, y) in enumerate(zip(a, b)):
            d = _first_diff_path(x, y, ign, f"{path}[{i}]" if path else f"[{i}]")
            if d is not None:
                return d
        return None
    if isinstance(a, dict):
        ka = set(a.keys()) - ign
        kb = set(b.keys()) - ign
        if ka != kb:
            return (f"{path}.keys" if path else "keys", sorted(ka), sorted(kb))
        for k in sorted(ka):
            d = _first_diff_path(a[k], b[k], ign, f"{path}.{k}" if path else k)
            if d is not None:
                return d
        return None
    return None if a == b else (path or "(root)", a, b)


def _truncate(val: object, max_len: int = 200) -> str:
    """Stringify value and truncate for console."""
    s = json.dumps(val, ensure_ascii=False)
    if len(s) <= max_len:
        return s
    return s[: max_len - 3] + "..."


def run(base_url: str, ignore_replay: bool = False) -> tuple[bool, str]:
    """
    Run reports test: load each *_team.json and *_group.json,
    replay request, compare response (unless ignore_replay). Returns (success, message).
    ignore_replay: if True, only live (assert 200); skip comparison to recording.
    """
    if not os.path.isdir(RECORDINGS_REPORTS_DIR):
        return True, "ok (no recordings)"

    client = BackendClient(base_url)
    files = sorted(
        f for f in os.listdir(RECORDINGS_REPORTS_DIR)
        if (f.endswith("_team.json") or f.endswith("_group.json"))
        and not _excluded_report(f.replace("_team.json", "").replace("_group.json", ""))
    )
    if not files:
        return True, "ok (no recording files)"

    failed = []
    for filename in files:
        path = os.path.join(RECORDINGS_REPORTS_DIR, filename)
        with open(path, encoding="utf-8") as f:
            rec = json.load(f)
        req = rec.get("request") or {}
        resp_rec = rec.get("response") or {}
        method = (req.get("method") or "GET").upper()
        path_str = req.get("path") or ""
        params = dict(req.get("query_params") or {})
        params["bypass_cache"] = "true"  # always bypass cache on replay

        if method != "GET" or not path_str:
            _fail(filename, "invalid request")
            failed.append(filename)
            continue

        r = client.get(path_str, params=params)
        label = filename.replace(".json", "")
        if ignore_replay:
            if r.status_code == 200:
                try:
                    body_live = r.json()
                    cnt = _get_result_count(body_live)
                    _pass(label, cnt)
                except Exception:
                    _pass(label)
            else:
                _fail(label, f"status {r.status_code}")
                failed.append(filename)
            continue

        status_ok = r.status_code == resp_rec.get("status")
        try:
            body_live = r.json()
        except Exception:
            body_live = r.text
        body_rec = resp_rec.get("body")
        body_ok = _deep_equal(body_live, body_rec, _IGNORED_BODY_KEYS)

        if status_ok and body_ok:
            cnt = _get_result_count(body_live)
            _pass(label, cnt)
        else:
            if not status_ok:
                _fail(label, f"status {r.status_code} != {resp_rec.get('status')}")
            else:
                diff = _first_diff_path(body_live, body_rec, _IGNORED_BODY_KEYS)
                detail = "body mismatch"
                if diff:
                    path_str, exp_val, act_val = diff
                    detail += f" at {path_str}: expected {_truncate(exp_val)} vs actual {_truncate(act_val)}"
                _fail(label, detail)
                # Write full expected/actual to failure artifacts for inspection
                failures_dir = os.path.join(RECORDINGS_REPORTS_DIR, "failures")
                try:
                    os.makedirs(failures_dir, exist_ok=True)
                    safe_label = label.replace(os.sep, "_").replace(" ", "_")
                    exp_path = os.path.join(failures_dir, f"{safe_label}_expected.json")
                    act_path = os.path.join(failures_dir, f"{safe_label}_actual.json")
                    with open(exp_path, "w", encoding="utf-8") as f:
                        json.dump(body_rec, f, indent=2, ensure_ascii=False)
                    with open(act_path, "w", encoding="utf-8") as f:
                        json.dump(body_live, f, indent=2, ensure_ascii=False)
                    print(f"      expected: {exp_path}")
                    print(f"      actual:   {act_path}")
                except OSError:
                    pass
            failed.append(filename)

    if failed:
        return False, f"{len(failed)} recording(s) failed"
    return True, "ok"


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument(
        "--base-url",
        default=os.environ.get("BACKEND_BASE_URL", "http://localhost:8000"),
        help="Backend base URL (default: env BACKEND_BASE_URL or http://localhost:8000)",
    )
    args = p.parse_args()
    base = args.base_url.rstrip("/")
    ok, msg = run(base)
    print()
    print(msg)
    sys.exit(0 if ok else 1)
