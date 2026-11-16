"""
Connection to the SAP S/4HANA system: base URL and authentication headers.

Two modes:
  destination  SAP_DESTINATION_NAME is set: the system is read from the SAP BTP
               Destination service bound to the app (VCAP_SERVICES, label
               "destination"). The Destination service handles the authentication
               (BasicAuthentication, OAuth2ClientCredentials, ...) and returns
               ready-to-use HTTP headers. Extra headers and query parameters come
               from the destination properties "URL.headers.<name>" and
               "URL.queries.<name>" (e.g. URL.headers.APIKey for the sandbox).
  direct       local development: SAP_API_BASE_URL + SAP_API_KEY from .env.

Credentials are never logged and never returned by describe().
"""

import json
import os
import threading
import time

import httpx
from dotenv import load_dotenv

load_dotenv()

DEFAULT_BASE_URL = "https://sandbox.api.sap.com/s4hanacloud"
DESTINATION_CACHE_SECONDS = 300
TIMEOUT_SECONDS = 15


class SAPConnectionError(Exception):
    """Configuration or Destination service error, safe to show to the user."""


def _destination_service_credentials():
    try:
        services = json.loads(os.getenv("VCAP_SERVICES", "{}"))
    except ValueError:
        return None
    instances = services.get("destination") or []
    return instances[0].get("credentials") if instances else None


class SAPConnection:

    def __init__(self):
        self.destination_name = os.getenv("SAP_DESTINATION_NAME", "").strip() or None
        self.mode = "destination" if self.destination_name else "direct"
        self._direct_url = os.getenv("SAP_API_BASE_URL", DEFAULT_BASE_URL).strip().rstrip("/")
        self._api_key = os.getenv("SAP_API_KEY", "").strip().strip('"')
        self._resolved = None          # (expires_at, base_url, headers, queries)
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def configured(self) -> bool:
        if self.mode == "destination":
            return _destination_service_credentials() is not None
        return bool(self._api_key)

    @property
    def missing_configuration(self) -> str:
        if self.mode == "destination":
            return "No Destination service is bound to the application."
        return "SAP_API_KEY is not configured in .env."

    def resolve(self):
        """(base_url, headers, query parameters) of the SAP system."""

        if self.mode == "direct":
            if not self._api_key:
                raise SAPConnectionError(self.missing_configuration)
            return self._direct_url, {"APIKey": self._api_key}, {}

        with self._lock:
            if self._resolved and self._resolved[0] > time.monotonic():
                return self._resolved[1:]
            self._resolved = self._read_destination()
            return self._resolved[1:]

    def invalidate(self):
        """Forget the cached destination, e.g. after an authentication error."""
        self._resolved = None

    @property
    def base_url(self):
        """System URL, or None when it cannot be resolved."""
        try:
            return self.resolve()[0]
        except SAPConnectionError:
            return None

    @property
    def is_sandbox(self):
        return "sandbox.api.sap.com" in (self.base_url or "")

    @property
    def label(self):
        return "SAP S/4HANA Cloud sandbox" if self.is_sandbox else "SAP S/4HANA Cloud"

    def describe(self):
        return {
            "mode": self.mode,
            "destination": self.destination_name,
            "system": self.base_url
        }

    # ------------------------------------------------------------------
    # Destination service
    # ------------------------------------------------------------------

    def _read_destination(self):
        credentials = _destination_service_credentials()
        if not credentials:
            raise SAPConnectionError(self.missing_configuration)

        try:
            token_response = httpx.post(
                credentials["url"].rstrip("/") + "/oauth/token",
                data={"grant_type": "client_credentials", "client_id": credentials["clientid"]},
                auth=(credentials["clientid"], credentials["clientsecret"]),
                timeout=TIMEOUT_SECONDS
            )
            token_response.raise_for_status()
            token = token_response.json()["access_token"]

            response = httpx.get(
                credentials["uri"].rstrip("/") + "/destination-configuration/v1/destinations/"
                + self.destination_name,
                headers={"Authorization": f"Bearer {token}"},
                timeout=TIMEOUT_SECONDS
            )
        except (httpx.HTTPError, KeyError, ValueError) as error:
            raise SAPConnectionError("The SAP BTP Destination service is not reachable.") from error

        if response.status_code == 404:
            raise SAPConnectionError(f"Destination '{self.destination_name}' not found in SAP BTP.")
        if response.status_code >= 400:
            raise SAPConnectionError(f"Destination service error (HTTP {response.status_code}).")

        body = response.json()
        configuration = body.get("destinationConfiguration") or {}

        if (configuration.get("ProxyType") or "Internet") != "Internet":
            raise SAPConnectionError(
                f"Destination '{self.destination_name}' uses proxy type {configuration.get('ProxyType')}: "
                "only Internet destinations are supported (no Cloud Connector)."
            )
        url = (configuration.get("URL") or "").rstrip("/")
        if not url:
            raise SAPConnectionError(f"Destination '{self.destination_name}' has no URL.")

        headers, queries = {}, {}
        lifetime = DESTINATION_CACHE_SECONDS
        for auth_token in body.get("authTokens") or []:
            if auth_token.get("error"):
                raise SAPConnectionError(
                    f"Destination '{self.destination_name}': authentication failed ({auth_token['error']})."
                )
            header = auth_token.get("http_header") or {}
            if header.get("key"):
                headers[header["key"]] = header.get("value", "")
            expires_in = auth_token.get("expires_in")
            if expires_in:
                lifetime = min(lifetime, max(int(expires_in) - 60, 30))

        for name, value in configuration.items():
            if name.startswith("URL.headers."):
                headers[name[len("URL.headers."):]] = value
            elif name.startswith("URL.queries."):
                queries[name[len("URL.queries."):]] = value

        return time.monotonic() + lifetime, url, headers, queries


_connection = None


def get_connection():
    global _connection
    if _connection is None:
        _connection = SAPConnection()
    return _connection
