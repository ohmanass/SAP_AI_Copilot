"""
Minimal read-only OData V4 client for SAP S/4HANA APIs.

Authentication uses the SAP Business Accelerator Hub "APIKey" header
(sandbox). The key is read from the environment and never logged.
"""

import os
import time
from urllib.parse import quote

import httpx

SAFE_CHARS = "$(),'=:;/-_."


class SAPODataError(Exception):
    """Error with a message that is safe to show to the user / the LLM."""


class ODataClient:

    CACHE_TTL_SECONDS = 60

    def __init__(self, service_url: str, api_key: str | None = None, timeout: float = 20):
        self.service_url = service_url.rstrip("/") + "/"
        self._api_key = (api_key or os.getenv("SAP_API_KEY", "")).strip().strip('"')
        self._timeout = timeout
        self._cache = {}

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def get(self, path: str, params: dict | None = None, use_cache: bool = True):
        """GET a resource and return the parsed JSON body."""

        if not self.configured:
            raise SAPODataError("SAP_API_KEY is not configured in .env.")

        key = (path, tuple(sorted((params or {}).items())))
        cached = self._cache.get(key)
        if use_cache and cached and time.monotonic() - cached[0] < self.CACHE_TTL_SECONDS:
            return cached[1]

        url = self.service_url + path
        if params:
            # Encode spaces as %20 (not "+"), keep OData syntax characters readable
            url += "?" + "&".join(
                f"{name}={quote(str(value), safe=SAFE_CHARS)}" for name, value in params.items()
            )

        try:
            response = httpx.get(
                url,
                headers={"APIKey": self._api_key, "Accept": "application/json"},
                timeout=self._timeout
            )
        except httpx.TimeoutException as error:
            raise SAPODataError("The SAP system did not answer in time.") from error
        except httpx.HTTPError as error:
            raise SAPODataError("The SAP system is not reachable from this machine.") from error

        if response.status_code == 401:
            raise SAPODataError("The SAP API key was rejected (HTTP 401).")
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
