"""Lightweight Datarhei Core API client using httpx directly.

Replaces the external core_client package to avoid its Pydantic
validation bugs and reduce dependencies.
"""

import json
import base64
import logging
from datetime import datetime
from typing import Optional

import httpx


class CoreAPIClient:
    """Minimal client for the Datarhei Core v3 API.

    Handles JWT login, token refresh, and the specific endpoints used
    by this application:
      - GET  /api/v3/process          (list processes)
      - GET  /api/v3/process/{id}     (get single process)
      - PUT  /api/v3/process/{id}/command  (start/stop/restart/reload)
    """

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        retries: int = 3,
        timeout: float = 10.0,
        logger: Optional[logging.Logger] = None,
    ):
        self.base_url = base_url.rstrip('/')
        self.username = username
        self.password = password
        self.retries = retries
        self.timeout = timeout
        self.logger = logger or logging.getLogger(__name__)

        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self._access_token_expires_at: Optional[int] = None

    # ------------------------------------------------------------------
    # Auth helpers
    # ------------------------------------------------------------------

    def _decode_token_expiry(self, token: str) -> int:
        """Extract 'exp' claim from a JWT without verification."""
        payload = token.split('.')[1]
        # JWT base64url may lack padding
        padded = payload + '=' * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded))
        return data['exp']

    def login(self) -> None:
        """Authenticate with username/password and store tokens."""
        resp = httpx.post(
            f'{self.base_url}/api/login',
            json={'username': self.username, 'password': self.password},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        body = resp.json()
        self.access_token = body['access_token']
        self.refresh_token = body.get('refresh_token')
        self._access_token_expires_at = self._decode_token_expiry(self.access_token)

    def _token_is_expired(self) -> bool:
        if self._access_token_expires_at is None:
            return True
        return datetime.fromtimestamp(self._access_token_expires_at) <= datetime.now()

    def _refresh_access_token(self) -> None:
        """Attempt to refresh the access token, falling back to full login."""
        if self.refresh_token:
            resp = httpx.get(
                f'{self.base_url}/api/login/refresh',
                headers={
                    'accept': 'application/json',
                    'authorization': f'Bearer {self.refresh_token}',
                },
                timeout=self.timeout,
            )
            if resp.status_code == 200:
                body = resp.json()
                if body.get('access_token'):
                    self.access_token = body['access_token']
                    self._access_token_expires_at = self._decode_token_expiry(self.access_token)
                    return
        # Refresh failed or unavailable — do a full login
        self.login()

    def _get_headers(self) -> dict:
        """Return auth headers, refreshing the token if needed."""
        if self._token_is_expired():
            self._refresh_access_token()
        return {
            'accept': 'application/json',
            'content-type': 'application/json',
            'authorization': f'Bearer {self.access_token}',
        }

    # ------------------------------------------------------------------
    # HTTP helper
    # ------------------------------------------------------------------

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """Make an authenticated request with retry support."""
        url = f'{self.base_url}{path}'
        headers = self._get_headers()
        transport = httpx.HTTPTransport(retries=self.retries)
        with httpx.Client(transport=transport, http2=True) as client:
            resp = client.request(
                method, url, headers=headers, timeout=self.timeout, **kwargs
            )
        resp.raise_for_status()
        return resp

    # ------------------------------------------------------------------
    # Process endpoints
    # ------------------------------------------------------------------

    def v3_process_get_list(self) -> list:
        """GET /api/v3/process — returns list of process dicts."""
        resp = self._request('GET', '/api/v3/process')
        return resp.json()

    def v3_process_get(self, id: str) -> dict:
        """GET /api/v3/process/{id} — returns a process dict."""
        resp = self._request('GET', f'/api/v3/process/{id}')
        return resp.json()

    def v3_process_put_command(self, id: str, command: str) -> dict:
        """PUT /api/v3/process/{id}/command — send start/stop/restart/reload."""
        resp = self._request('PUT', f'/api/v3/process/{id}/command', json={'command': command})
        return resp.json()

    def v3_process_get_report(self, id: str) -> dict:
        """GET /api/v3/process/{id}/report — returns detailed process report including I/O stats."""
        resp = self._request('GET', f'/api/v3/process/{id}/report')
        return resp.json()

    def v3_process_get_state(self, id: str) -> dict:
        """GET /api/v3/process/{id}/state — returns detailed process state information."""
        resp = self._request('GET', f'/api/v3/process/{id}/state')
        return resp.json()

    def v3_process_get_probe(self, id: str) -> dict:
        """GET /api/v3/process/{id}/probe — returns FFprobe information about inputs."""
        resp = self._request('GET', f'/api/v3/process/{id}/probe')
        return resp.json()

    def v3_process_get_config(self, id: str) -> dict:
        """GET /api/v3/process/{id}/config — returns process configuration."""
        resp = self._request('GET', f'/api/v3/process/{id}/config')
        return resp.json()
