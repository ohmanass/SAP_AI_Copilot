"""
The SAPUI5 interface served by the backend itself (local development).

Locally, one server on one port serves the interface (frontend/webapp) and the
API (/api): no second server, no proxy, no CORS. The SAPUI5 library comes from
the SAP CDN (see index.html). Files are read from disk at every request and
sent with "Cache-Control: no-cache": after a change, a refresh of the page
(F5) shows it.

On SAP BTP the backend is deployed without the frontend: the Application Router
serves the interface and handles the SAP login, so nothing is mounted.
"""

import os
from pathlib import Path

from starlette.staticfiles import StaticFiles

FRONTEND = Path(__file__).resolve().parents[1] / "frontend" / "webapp"


class FreshStaticFiles(StaticFiles):
    """Static files the browser always revalidates (304 when unchanged)."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


def mount_frontend(app, directory: Path = FRONTEND) -> bool:
    """Serve the interface on "/" when its sources are there (never on SAP BTP). Call after every API route."""
    if os.getenv("SERVE_FRONTEND", "auto").lower() in ("0", "false", "off") or not directory.is_dir():
        return False
    app.mount("/", FreshStaticFiles(directory=directory, html=True), name="frontend")
    return True
