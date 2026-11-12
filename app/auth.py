"""
XSUAA authentication for the API on SAP BTP.

The SAP Application Router forwards the signed-in user's JWT
(destination option "forwardAuthToken"). This ASGI middleware validates it
with SAP's official library (sap-xssec): signature, expiry and audience.

Active only when an XSUAA service is bound to the app (VCAP_SERVICES);
locally there is no binding and every request goes through unchanged.
"""

import json
import logging
import os

from anyio import to_thread

logger = logging.getLogger("sap_ai_copilot.auth")

# Cloud Foundry health checks call this endpoint without a token
PUBLIC_PATHS = {"/api/health"}


def xsuaa_credentials():
    try:
        services = json.loads(os.getenv("VCAP_SERVICES", "{}"))
    except ValueError:
        return None

    for instance in services.get("xsuaa", []):
        return instance.get("credentials")
    return None


class XsuaaAuthMiddleware:

    def __init__(self, app, credentials):
        self.app = app
        self.credentials = credentials

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or not scope["path"].startswith("/api/")
            or scope["path"] in PUBLIC_PATHS
        ):
            await self.app(scope, receive, send)
            return

        token = self._bearer_token(scope)
        if not token:
            await self._reject(send, "Missing bearer token.")
            return

        try:
            # May fetch the XSUAA signing keys (cached): run outside the event loop
            await to_thread.run_sync(self._validate, token)
        except Exception as error:
            logger.warning("Rejected token: %s", type(error).__name__)
            await self._reject(send, "Invalid or expired token.")
            return

        await self.app(scope, receive, send)

    def _validate(self, token):
        from sap import xssec

        xssec.create_security_context(token, self.credentials)

    @staticmethod
    def _bearer_token(scope):
        for name, value in scope.get("headers", []):
            if name == b"authorization":
                header = value.decode("latin-1")
                if header.lower().startswith("bearer "):
                    return header[7:].strip()
        return None

    @staticmethod
    async def _reject(send, message):
        body = json.dumps({"detail": message}).encode()
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"www-authenticate", b"Bearer"),
                (b"content-length", str(len(body)).encode())
            ]
        })
        await send({"type": "http.response.body", "body": body})


def install(app):
    """Protect the FastAPI app when running with an XSUAA binding."""

    credentials = xsuaa_credentials()
    if credentials:
        app.add_middleware(XsuaaAuthMiddleware, credentials=credentials)
        logger.info("XSUAA authentication enabled")
    return bool(credentials)
