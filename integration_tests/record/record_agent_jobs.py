#!/usr/bin/env python3
"""
Record agent jobs flow (same as test_agent_jobs): POST create (Test) -> GET list -> GET {id} -> POST claim-next -> PATCH.
"""

from __future__ import annotations

import argparse
import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_SCRIPT_DIR))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from integration_tests.client import BackendClient
from integration_tests.record.record_common import save_recording

PREFIX = "/api/v1"


def _resp_body(r) -> object:
    try:
        return r.json()
    except Exception:
        return r.text


def main() -> int:
    parser = argparse.ArgumentParser(description="Record agent jobs flow (create Test job, list, get, claim-next, patch)")
    parser.add_argument("--base-url", default=os.environ.get("BACKEND_BASE_URL", "http://localhost:8000"))
    parser.add_argument("--out", required=True, help="Output directory for recordings")
    args = parser.parse_args()
    client = BackendClient(args.base_url.rstrip("/"))
    out_dir = args.out

    # 1. POST /agent-jobs/create
    body = {"job_type": "Test"}
    r = client.post(f"{PREFIX}/agent-jobs/create", json=body)
    save_recording(out_dir, "POST", f"{PREFIX}/agent-jobs/create", None, body, r.status_code, _resp_body(r))
    job_id = None
    if r.status_code == 200:
        data = r.json()
        job = (data.get("data") or {}).get("job") if isinstance(data, dict) else {}
        job_id = job.get("job_id") if isinstance(job, dict) else None

    if job_id is None:
        print("Agent jobs: create failed, skipping rest")
        return 1

    # 2. GET /agent-jobs
    r = client.get(f"{PREFIX}/agent-jobs")
    save_recording(out_dir, "GET", f"{PREFIX}/agent-jobs", None, None, r.status_code, _resp_body(r))

    # 3. GET /agent-jobs/{job_id}
    r = client.get(f"{PREFIX}/agent-jobs/{job_id}")
    save_recording(out_dir, "GET", f"{PREFIX}/agent-jobs/{job_id}", None, None, r.status_code, _resp_body(r))

    # 4. POST /agent-jobs/claim-next
    body = {"claimed_by": "integration-test"}
    r = client.post(f"{PREFIX}/agent-jobs/claim-next", json=body)
    save_recording(out_dir, "POST", f"{PREFIX}/agent-jobs/claim-next", None, body, r.status_code, _resp_body(r))

    # 5. PATCH /agent-jobs/{job_id}
    body = {"status": "completed", "result": "integration test ok"}
    r = client.patch(f"{PREFIX}/agent-jobs/{job_id}", json=body)
    save_recording(out_dir, "PATCH", f"{PREFIX}/agent-jobs/{job_id}", None, body, r.status_code, _resp_body(r))

    print(f"Agent jobs: recorded to {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
