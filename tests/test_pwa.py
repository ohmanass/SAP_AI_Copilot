"""Installable app (PWA): manifest, icons, service worker, and what is public on SAP BTP."""

import json
import re
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
WEBAPP = ROOT / "frontend" / "webapp"


def manifest():
    return json.loads((WEBAPP / "app.webmanifest").read_text(encoding="utf-8"))


def test_manifest_makes_the_app_installable():
    app = manifest()
    assert app["name"] == "SAP AI Copilot" and app["short_name"]
    assert app["display"] == "standalone" and app["start_url"] == "./"
    sizes = {(icon["sizes"], icon.get("purpose", "any")) for icon in app["icons"]}
    assert {("192x192", "any"), ("512x512", "any"), ("512x512", "maskable")} <= sizes


@pytest.mark.parametrize("icon", [i for i in json.loads((WEBAPP / "app.webmanifest").read_text(encoding="utf-8"))["icons"]
                                  if i["type"] == "image/png"])
def test_every_png_icon_exists_with_its_declared_size(icon):
    width, height = map(int, icon["sizes"].split("x"))
    assert Image.open(WEBAPP / icon["src"]).size == (width, height)


def test_index_links_the_manifest_and_the_ios_icon():
    index = (WEBAPP / "index.html").read_text(encoding="utf-8")
    assert '<link rel="manifest" href="app.webmanifest">' in index
    assert Image.open(WEBAPP / "img" / "apple-touch-icon.png").size == (180, 180)


def test_the_service_worker_never_keeps_sap_data():
    worker = (WEBAPP / "sw.js").read_text(encoding="utf-8")
    cached = re.search(r"OFFLINE_FILES = \[([^\]]*)\]", worker).group(1)
    assert sorted(re.findall(r'"([^"]+)"', cached)) == ["img/logo.svg", "offline.js"]   # + OFFLINE_PAGE
    assert "/api" not in worker and "cache.put" not in worker


def test_only_the_install_files_are_public_on_btp():
    routes = json.loads((ROOT / "deploy" / "approuter" / "xs-app.json").read_text(encoding="utf-8"))["routes"]
    public = [route["source"] for route in routes if route.get("authenticationType") == "none"]
    assert len(public) == 1
    for path in ("/app.webmanifest", "/sw.js", "/offline.html", "/offline.js", "/img/icon-192.png", "/img/logo.svg"):
        assert re.match(public[0], path), path
    for path in ("/", "/index.html", "/Component-preload.js", "/manifest.json", "/i18n/i18n.properties",
                 "/api/sessions", "/img/../index.html", "/img/sub/x.png"):
        assert not re.match(public[0], path), path


def test_the_manifest_is_served_with_its_media_type():
    from fastapi.testclient import TestClient
    from app import api

    response = TestClient(api.app).get("/app.webmanifest")
    assert response.status_code == 200 and response.headers["content-type"].startswith("application/manifest+json")
