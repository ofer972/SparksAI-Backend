"""
Agent jobs integration test: all five endpoints in one flow.
Create (POST) -> List (GET) -> Get by id (GET) -> Claim next (POST) -> Update (PATCH).
"""

from __future__ import annotations

from integration_tests.client import BackendClient

_G = "\033[32m"   # green
_R = "\033[31m"   # red
_N = "\033[0m"    # reset


def _pass(endpoint: str, count: int | None = None) -> None:
    if count is not None:
        print(f"  {_G}PASS{_N} {endpoint} count={count}")
    else:
        print(f"  {_G}PASS{_N} {endpoint}")


def _fail(endpoint: str, msg: str) -> None:
    print(f"  {_R}FAIL{_N} {endpoint}: {msg}")


def run(base_url: str, ignore_replay: bool = False) -> tuple[bool, str]:
    """
    Run the agent jobs test. Returns (success, message).
    ignore_replay: if True, only live assertions; skip replay (no recording comparison).
    """
    client = BackendClient(base_url)
    prefix = "/api/v1"

    # 1. POST /agent-jobs/create
    r = client.post(f"{prefix}/agent-jobs/create", json={"job_type": "Test"})
    if r.status_code != 200:
        _fail("POST /agent-jobs/create", f"{r.status_code}")
        return False, f"create: expected 200, got {r.status_code} - {r.text}"
    try:
        data = r.json()
        if not data.get("success") or "data" not in data or "job" not in data["data"]:
            _fail("POST /agent-jobs/create", "bad shape")
            return False, f"create: unexpected response shape - {data}"
        job = data["data"]["job"]
        job_id = job.get("job_id")
        if job_id is None:
            _fail("POST /agent-jobs/create", "no job_id")
            return False, "create: response missing job_id"
        if str(job.get("status", "")).lower() != "pending":
            _fail("POST /agent-jobs/create", f"status {job.get('status')}")
            return False, f"create: expected status Pending, got {job.get('status')}"
    except Exception as e:
        _fail("POST /agent-jobs/create", str(e))
        return False, f"create: {e}"
    _pass("POST /agent-jobs/create")

    # 2. GET /agent-jobs
    r = client.get(f"{prefix}/agent-jobs")
    if r.status_code != 200:
        _fail("GET /agent-jobs", f"{r.status_code}")
        return False, f"list: expected 200, got {r.status_code} - {r.text}"
    try:
        data = r.json()
        if not data.get("success") or "data" not in data or "jobs" not in data["data"]:
            _fail("GET /agent-jobs", "bad shape")
            return False, "list: unexpected response shape"
        jobs = data["data"]["jobs"]
        found = next((j for j in jobs if j.get("job_id") == job_id), None)
        if not found:
            _fail("GET /agent-jobs", f"job {job_id} not in list")
            return False, f"list: created job_id {job_id} not found in list"
    except Exception as e:
        _fail("GET /agent-jobs", str(e))
        return False, f"list: {e}"
    _pass("GET /agent-jobs", len(jobs))

    # 3. GET /agent-jobs/{job_id}
    r = client.get(f"{prefix}/agent-jobs/{job_id}")
    if r.status_code != 200:
        _fail("GET /agent-jobs/{id}", f"{r.status_code}")
        return False, f"get: expected 200, got {r.status_code} - {r.text}"
    try:
        data = r.json()
        if not data.get("success") or data["data"]["job"].get("job_id") != job_id:
            _fail("GET /agent-jobs/{id}", "wrong job")
            return False, "get: wrong job or missing data"
        if str(data["data"]["job"].get("status", "")).lower() != "pending":
            _fail("GET /agent-jobs/{id}", f"status {data['data']['job'].get('status')}")
            return False, f"get: expected status Pending, got {data['data']['job'].get('status')}"
    except Exception as e:
        _fail("GET /agent-jobs/{id}", str(e))
        return False, f"get: {e}"
    _pass("GET /agent-jobs/{id}")

    # 4. POST /agent-jobs/claim-next
    r = client.post(f"{prefix}/agent-jobs/claim-next", json={"claimed_by": "integration-test"})
    if r.status_code not in (200, 204):
        _fail("POST /agent-jobs/claim-next", f"{r.status_code}")
        return False, f"claim-next: expected 200 or 204, got {r.status_code} - {r.text}"
    if r.status_code == 200:
        try:
            data = r.json()
            claimed_job = data.get("data", {}).get("job", {})
            if claimed_job.get("job_id") != job_id:
                pass  # claimed another job; our Test job still pending, continue
            else:
                if str(claimed_job.get("status", "")).lower() != "claimed":
                    _fail("POST /agent-jobs/claim-next", f"status {claimed_job.get('status')}")
                    return False, f"claim-next: expected status claimed, got {claimed_job.get('status')}"
                if claimed_job.get("claimed_by") != "integration-test":
                    _fail("POST /agent-jobs/claim-next", f"claimed_by {claimed_job.get('claimed_by')}")
                    return False, f"claim-next: expected claimed_by integration-test, got {claimed_job.get('claimed_by')}"
        except Exception as e:
            _fail("POST /agent-jobs/claim-next", str(e))
            return False, f"claim-next: {e}"
    _pass("POST /agent-jobs/claim-next")

    # 5. PATCH /agent-jobs/{job_id}
    r = client.patch(
        f"{prefix}/agent-jobs/{job_id}",
        json={"status": "completed", "result": "integration test ok"},
    )
    if r.status_code != 200:
        _fail("PATCH /agent-jobs/{id}", f"{r.status_code}")
        return False, f"patch: expected 200, got {r.status_code} - {r.text}"
    try:
        data = r.json()
        job = data.get("data", {}).get("job", {})
        if str(job.get("status", "")).lower() != "completed":
            _fail("PATCH /agent-jobs/{id}", f"status {job.get('status')}")
            return False, f"patch: expected status completed, got {job.get('status')}"
        if job.get("result") != "integration test ok":
            _fail("PATCH /agent-jobs/{id}", f"result {job.get('result')}")
            return False, f"patch: expected result 'integration test ok', got {job.get('result')}"
    except Exception as e:
        _fail("PATCH /agent-jobs/{id}", str(e))
        return False, f"patch: {e}"
    _pass("PATCH /agent-jobs/{id}")

    return True, "ok"
