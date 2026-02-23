"""
Shared helpers for record scripts: save request+response to JSON (same format as reports).
"""

from __future__ import annotations

import json
import os
import re


def save_recording(
    out_dir: str,
    method: str,
    path: str,
    query_params: dict | None,
    body: dict | None,
    status: int,
    response_body: object,
    slug_extra: str = "",
) -> str:
    """Write one recording file. Returns path to file. response_body: parsed JSON or str."""
    path_norm = path if path.startswith("/") else "/" + path
    slug = method.upper() + "_" + path_norm.strip("/").replace("/", "_")
    if slug_extra:
        slug += "_" + str(slug_extra)
    slug = re.sub(r"[^\w\-.]", "_", slug)
    filename = slug + ".json"
    filepath = os.path.join(out_dir, filename)
    rec = {
        "request": {
            "method": method,
            "path": path_norm,
            "query_params": query_params if query_params is not None else {},
        },
        "response": {"status": status, "body": response_body},
    }
    if body is not None:
        rec["request"]["body"] = body
    os.makedirs(out_dir, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=2, ensure_ascii=False)
    return filepath
