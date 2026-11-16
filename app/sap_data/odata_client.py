"""
Minimal read-only OData client (V2 and V4) for SAP S/4HANA APIs.

The system URL and the authentication headers come from the SAP connection
(app.sap_data.connection): BTP Destination service, or SAP_API_BASE_URL +
SAP_API_KEY locally. Credentials are never logged.
"""

import time
from urllib.parse import quote

import httpx

from app.sap_data.connection import SAPConnectionError, get_connection

SAFE_CHARS = "$(),'=:;/-_."


class SAPODataError(Exception):
    """Error with a message that is safe to show to the user / the LLM."""


class ODataClient:

    CACHE_TTL_SECONDS = 60

    def __init__(self, service_path: str, connection=None, timeout: float = 20):
        """service_path: path of the OData service, relative to the system URL."""
        self.service_path = service_path.strip("/") + "/"
        self.connection = connection or get_connection()
        self._timeout = timeout
        self._cache = {}

    @property
    def configured(self) -> bool:
        return self.connection.configured

    def get(self, path: str, params: dict | None = None, use_cache: bool = True):
        """GET a resource and return the parsed JSON body."""

        if not self.configured:
            raise SAPODataError(self.connection.missing_configuration)

        key = (path, tuple(sorted((params or {}).items())))
        cached = self._cache.get(key)
        if use_cache and cached and time.monotonic() - cached[0] < self.CACHE_TTL_SECONDS:
            return cached[1]

        try:
            base_url, headers, queries = self.connection.resolve()
        except SAPConnectionError as error:
            raise SAPODataError(str(error)) from error

        url = f"{base_url}/{self.service_path}{path}"
        params = {**queries, **(params or {})}
        if params:
            # Encode spaces as %20 (not "+"), keep OData syntax characters readable
            url += "?" + "&".join(
                f"{name}={quote(str(value), safe=SAFE_CHARS)}" for name, value in params.items()
            )

        try:
            response = httpx.get(
                url,
                headers={**headers, "Accept": "application/json"},
                timeout=self._timeout
            )
        except httpx.TimeoutException as error:
            raise SAPODataError("The SAP system did not answer in time.") from error
        except httpx.HTTPError as error:
            raise SAPODataError("The SAP system is not reachable from this machine.") from error

        if response.status_code in (401, 403):
            # Credentials may have changed or a token expired: read the destination again next time
            self.connection.invalidate()
            raise SAPODataError(f"The SAP system rejected the credentials (HTTP {response.status_code}).")
        if response.status_code == 404:
            raise SAPODataError("Not found in the SAP system (HTTP 404).")
        if response.status_code == 429:
            raise SAPODataError("SAP API rate limit reached, please retry later (HTTP 429).")
        if response.status_code >= 400:
            message = ""
            try:
                message = response.json().get("error", {}).get("message", "")
            except ValueError:
                pass
            raise SAPODataError(f"SAP OData error (HTTP {response.status_code}). {message}".strip())

        body = response.json()
        self._cache[key] = (time.monotonic(), body)
        return body
