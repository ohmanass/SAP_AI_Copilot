"""User identity taken from the validated XSUAA token (never from the browser)."""

import asyncio

from app import auth


class Context:
    def __init__(self, logon_name, origin="sap.default", clientid="sb-sap-ai-copilot"):
        self._logon, self._origin, self._client = logon_name, origin, clientid

    def get_logon_name(self):
        return self._logon

    def get_origin(self):
        return self._origin

    def get_clientid(self):
        return self._client


def test_identity_combines_identity_provider_and_logon_name():
    assert auth.user_identity(Context("alice@example.com")) == "sap.default/alice@example.com"
    # Same logon name in another identity provider: another user
    assert auth.user_identity(Context("alice@example.com", origin="corporate-ias")) == "corporate-ias/alice@example.com"
    # Technical token without user
    assert auth.user_identity(Context(None)) == "client/sb-sap-ai-copilot"


class Request:
    def __init__(self, user=None):
        self.state = type("State", (), {})()
        if user:
            self.state.user = user


def test_current_user_defaults_to_the_local_user():
    assert auth.current_user(Request()) == auth.LOCAL_USER
    assert auth.current_user(Request("sap.default/alice@example.com")) == "sap.default/alice@example.com"


def test_middleware_sets_the_validated_user_and_ignores_headers(monkeypatch):
    seen = {}

    async def app(scope, receive, send):
        seen["user"] = scope["state"]["user"]

    middleware = auth.XsuaaAuthMiddleware(app, credentials={})
    monkeypatch.setattr(middleware, "_validate", lambda token: ("sap.default/alice@example.com", False))
    scope = {
        "type": "http",
        "path": "/api/sessions",
        "headers": [(b"authorization", b"Bearer token"), (b"x-user", b"sap.default/bob@example.com")]
    }
    asyncio.run(middleware(scope, None, None))
    assert seen["user"] == "sap.default/alice@example.com"


def test_middleware_rejects_requests_without_token():
    sent = []

    async def send(message):
        sent.append(message)

    async def app(scope, receive, send):
        raise AssertionError("must not be called")

    middleware = auth.XsuaaAuthMiddleware(app, credentials={})
    asyncio.run(middleware({"type": "http", "path": "/api/sessions", "headers": []}, None, send))
    assert sent[0]["status"] == 401
