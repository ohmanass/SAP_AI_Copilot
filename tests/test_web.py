"""One local server for the interface and the API (app/web.py), SAPUI5 from the SAP CDN."""

import re
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import web

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"


def test_the_api_server_also_serves_the_interface():
    from app import api

    client = TestClient(api.app)
    page = client.get("/")
    assert page.status_code == 200 and "sap-ui-bootstrap" in page.text
    assert page.headers["cache-control"] == "no-cache"          # F5 shows a change of the interface
    assert client.get("/i18n/i18n_fr.properties").status_code == 200

    # The API keeps precedence and answers JSON
    assert client.get("/api/health").json()["status"] == "online"
    assert client.get("/api/unknown-route").status_code == 404


def test_the_interface_is_not_served_without_its_sources_or_when_disabled(tmp_path, monkeypatch):
    assert web.mount_frontend(FastAPI(), tmp_path / "missing") is False     # SAP BTP: backend without frontend
    monkeypatch.setenv("SERVE_FRONTEND", "off")
    assert web.mount_frontend(FastAPI(), FRONTEND / "webapp") is False


def test_index_loads_the_sapui5_version_of_ui5_yaml_from_the_sap_cdn():
    version = re.search(r'(?m)^\s+version:\s*"([^"]+)"', (FRONTEND / "ui5.yaml").read_text(encoding="utf-8")).group(1)
    index = (FRONTEND / "webapp" / "index.html").read_text(encoding="utf-8")
    assert f'src="https://ui5.sap.com/{version}/resources/sap-ui-core.js"' in index
