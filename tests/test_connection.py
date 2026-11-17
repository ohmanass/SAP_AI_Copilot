"""SAP connection: direct mode (.env) and SAP BTP Destination service, with simulated HTTP answers."""

import json

import httpx
import pytest

from app.sap_data import connection as connection_module
from app.sap_data.connection import SAPConnection, SAPConnectionError
from app.sap_data.odata_client import ODataClient, SAPODataError

DESTINATION_CREDENTIALS = {
    "clientid": "client", "clientsecret": "secret",
    "url": "https://subaccount.authentication.eu10.hana.ondemand.com",
    "uri": "https://destination-configuration.cfapps.eu10.hana.ondemand.com"
}


def bind_destination_service(monkeypatch, name="s4"):
    monkeypatch.setenv("SAP_DESTINATION_NAME", name)
    monkeypatch.setenv("VCAP_SERVICES", json.dumps(
        {"destination": [{"name": "sap-ai-copilot-destination", "credentials": DESTINATION_CREDENTIALS}]}
    ))


class FakeDestinationService:
    """Simulated XSUAA token endpoint and Destination service 'find destination' API."""

    def __init__(self, monkeypatch, body=None, status_code=200):
        self.body = body
        self.status_code = status_code
        self.lookups = 0
        monkeypatch.setattr(connection_module.httpx, "post", self.post)
        monkeypatch.setattr(connection_module.httpx, "get", self.get)

    def post(self, url, **kwargs):
        assert url == DESTINATION_CREDENTIALS["url"] + "/oauth/token"
        return httpx.Response(200, json={"access_token": "token"}, request=httpx.Request("POST", url))

    def get(self, url, headers=None, **kwargs):
        assert headers == {"Authorization": "Bearer token"}
        self.lookups += 1
        return httpx.Response(self.status_code, json=self.body or {}, request=httpx.Request("GET", url))


def destination(**configuration):
    return {"destinationConfiguration": {"Name": "s4", "Type": "HTTP", "URL": "https://my-s4.example.com/",
                                         "ProxyType": "Internet", **configuration}}


# ---------------------------------------------------------------------------
# Direct mode (local development)
# ---------------------------------------------------------------------------

def test_direct_mode_uses_the_api_key():
    connection = SAPConnection()
    assert connection.mode == "direct"
    assert connection.resolve() == ("https://sandbox.api.sap.com/s4hanacloud", {"APIKey": "test-key"}, {})
    assert connection.label == "SAP S/4HANA Cloud sandbox"
    assert connection.describe() == {"mode": "direct", "destination": None,
                                     "system": "https://sandbox.api.sap.com/s4hanacloud"}


def test_direct_mode_without_key(monkeypatch):
    monkeypatch.setenv("SAP_API_KEY", "")
    connection = SAPConnection()
    assert not connection.configured
    with pytest.raises(SAPConnectionError, match="SAP_API_KEY"):
        connection.resolve()
    assert connection.base_url is None


# ---------------------------------------------------------------------------
# Destination mode (SAP BTP)
# ---------------------------------------------------------------------------

def test_destination_without_bound_service(monkeypatch):
    monkeypatch.setenv("SAP_DESTINATION_NAME", "s4")
    connection = SAPConnection()
    assert connection.mode == "destination"
    assert not connection.configured
    with pytest.raises(SAPConnectionError, match="No Destination service"):
        connection.resolve()


def test_destination_headers_and_queries(monkeypatch):
    bind_destination_service(monkeypatch)
    body = destination(**{"URL.headers.APIKey": "key", "URL.queries.sap-client": "100"})
    body["authTokens"] = [{"type": "Basic", "value": "dXNlcjpwdw==",
                           "http_header": {"key": "Authorization", "value": "Basic dXNlcjpwdw=="}}]
    FakeDestinationService(monkeypatch, body)

    base_url, headers, queries = SAPConnection().resolve()
    assert base_url == "https://my-s4.example.com"
    assert headers == {"Authorization": "Basic dXNlcjpwdw==", "APIKey": "key"}
    assert queries == {"sap-client": "100"}


def test_destination_is_cached_until_invalidated(monkeypatch):
    bind_destination_service(monkeypatch)
    service = FakeDestinationService(monkeypatch, destination())
    connection = SAPConnection()
    connection.resolve()
    connection.resolve()
    assert service.lookups == 1
    connection.invalidate()
    connection.resolve()
    assert service.lookups == 2


def test_short_token_lifetime_shortens_the_cache(monkeypatch):
    bind_destination_service(monkeypatch)
    body = destination()
    body["authTokens"] = [{"type": "Bearer", "value": "t", "expires_in": "100",
                           "http_header": {"key": "Authorization", "value": "Bearer t"}}]
    FakeDestinationService(monkeypatch, body)
    times = iter([1000.0, 1000.0])
    monkeypatch.setattr(connection_module.time, "monotonic", lambda: next(times))
    connection = SAPConnection()
    connection.resolve()
    # Refreshed 60 s before the token expires
    assert connection._resolved[0] == 1040.0


def test_destination_not_found(monkeypatch):
    bind_destination_service(monkeypatch, "missing")
    FakeDestinationService(monkeypatch, status_code=404)
    with pytest.raises(SAPConnectionError, match="'missing' not found"):
        SAPConnection().resolve()


def test_on_premise_destination_is_rejected(monkeypatch):
    bind_destination_service(monkeypatch)
    FakeDestinationService(monkeypatch, destination(ProxyType="OnPremise"))
    with pytest.raises(SAPConnectionError, match="Cloud Connector"):
        SAPConnection().resolve()


def test_authentication_error_of_the_destination(monkeypatch):
    bind_destination_service(monkeypatch)
    body = destination()
    body["authTokens"] = [{"type": "", "value": "", "error": "Retrieval of OAuth token failed"}]
    FakeDestinationService(monkeypatch, body)
    with pytest.raises(SAPConnectionError, match="authentication failed"):
        SAPConnection().resolve()


def test_unreachable_destination_service(monkeypatch):
    bind_destination_service(monkeypatch)

    def fail(*args, **kwargs):
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(connection_module.httpx, "post", fail)
    with pytest.raises(SAPConnectionError, match="not reachable"):
        SAPConnection().resolve()


# ---------------------------------------------------------------------------
# OData client on top of the connection
# ---------------------------------------------------------------------------

class StaticConnection:

    configured = True
    missing_configuration = ""

    def __init__(self):
        self.invalidated = False

    def resolve(self):
        return "https://my-s4.example.com", {"APIKey": "key"}, {"sap-client": "100"}

    def invalidate(self):
        self.invalidated = True


def test_odata_client_builds_the_request(monkeypatch):
    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append((url, headers))
        return httpx.Response(200, json={"value": []}, request=httpx.Request("GET", url))

    monkeypatch.setattr("app.sap_data.odata_client.httpx.get", fake_get)
    client = ODataClient("sap/opu/odata4/service/0001/", connection=StaticConnection())
    client.get("PurchaseOrder", {"$top": 1, "$filter": "Supplier eq 'A B'"})
    client.get("PurchaseOrder", {"$top": 1, "$filter": "Supplier eq 'A B'"})   # served from the cache

    assert len(calls) == 1
    url, headers = calls[0]
    assert url == ("https://my-s4.example.com/sap/opu/odata4/service/0001/PurchaseOrder"
                   "?sap-client=100&$top=1&$filter=Supplier%20eq%20'A%20B'")
    assert headers == {"APIKey": "key", "Accept": "application/json"}


@pytest.mark.parametrize("status_code", [401, 403])
def test_rejected_credentials_invalidate_the_destination(monkeypatch, status_code):
    monkeypatch.setattr("app.sap_data.odata_client.httpx.get",
                        lambda url, **kwargs: httpx.Response(status_code, request=httpx.Request("GET", url)))
    connection = StaticConnection()
    with pytest.raises(SAPODataError, match=f"HTTP {status_code}"):
        ODataClient("service/", connection=connection).get("Entity")
    assert connection.invalidated
