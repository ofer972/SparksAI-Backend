"""
Shared HTTP client for integration tests.
Calls the backend over HTTP; no backend code is modified.
"""

import requests


class BackendClient:
    """Simple HTTP client for SparksAI backend (GET, POST, PATCH)."""

    def __init__(self, base_url: str, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
        })

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return f"{self.base_url}{path}"

    def get(self, path: str, params: dict | None = None) -> requests.Response:
        return self.session.get(
            self._url(path),
            params=params,
            timeout=self.timeout,
        )

    def post(self, path: str, json: dict | None = None) -> requests.Response:
        return self.session.post(
            self._url(path),
            json=json,
            timeout=self.timeout,
        )

    def patch(self, path: str, json: dict | None = None) -> requests.Response:
        return self.session.patch(
            self._url(path),
            json=json,
            timeout=self.timeout,
        )
