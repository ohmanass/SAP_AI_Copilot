"""
Frontend tests (SAPUI5): QUnit unit tests and OPA5 journeys, run in a headless
Chrome or Edge from pytest, so "python -m pytest" tests the backend and the
interface in one command, locally and in the GitHub Actions CI.

The pages are frontend/webapp/test (SAPUI5 Test Starter). They load SAPUI5 from the SAP
CDN (internet needed) and publish their result on window.__testResult
(test/TestResult.js). The OPA journeys simulate the backend (test/integration/mock):
no API, SAP system or model is called.

Without Chrome or Edge the tests are skipped locally, but fail in the CI.
Set BROWSER_PATH to use a given browser.
"""

import asyncio
import functools
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

WEBAPP = Path(__file__).resolve().parent.parent / "frontend" / "webapp"
PAGES = {
    "unit": "test/Test.qunit.html?testsuite=test-resources/com/sapaicopilot/app/testsuite.qunit&test=unit/unitTests",
    "integration": "test/Test.qunit.html?testsuite=test-resources/com/sapaicopilot/app/testsuite.qunit&test=integration/opaTests",
}
PAGE_TIMEOUT = 300

BROWSER_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge",
)


def find_browser():
    candidates = [os.getenv("BROWSER_PATH")] + list(BROWSER_CANDIDATES)
    for candidate in filter(None, candidates):
        path = candidate if Path(candidate).is_file() else shutil.which(candidate)
        if path:
            return path
    return None


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


@pytest.fixture(scope="module")
def webapp_url():
    """frontend/webapp served on a free local port (static files only: the tests simulate the API)."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(WEBAPP)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/"
    server.shutdown()


@pytest.fixture(scope="module")
def browser():
    """Headless Chrome or Edge with the DevTools protocol on a free port."""
    path = find_browser()
    if not path:
        if os.getenv("CI"):
            pytest.fail("Chrome or Edge is required for the frontend tests in the CI")
        pytest.skip("No Chrome or Edge found: frontend tests skipped (set BROWSER_PATH)")

    profile = tempfile.mkdtemp(prefix="sap-ai-copilot-browser-")
    process = subprocess.Popen(
        [path, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
         "--remote-debugging-port=0", f"--user-data-dir={profile}", "--lang=en-US", "--window-size=1400,1000",
         *(["--no-sandbox"] if os.getenv("CI") else []), "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    port_file = Path(profile) / "DevToolsActivePort"
    for _ in range(100):
        if port_file.is_file() and port_file.read_text().strip():
            break
        time.sleep(0.1)
    port = int(port_file.read_text().splitlines()[0])
    yield f"http://127.0.0.1:{port}"
    process.kill()
    process.wait()
    shutil.rmtree(profile, ignore_errors=True)


async def run_page(devtools, url):
    import websockets

    target = json.loads(urllib.request.urlopen(
        urllib.request.Request(f"{devtools}/json/new?about:blank", method="PUT")).read())
    try:
        async with websockets.connect(target["webSocketDebuggerUrl"], max_size=None) as socket:
            ids = iter(range(1, 1_000_000))

            async def call(method, params=None):
                message_id = next(ids)
                await socket.send(json.dumps({"id": message_id, "method": method, "params": params or {}}))
                while True:
                    message = json.loads(await socket.recv())
                    if message.get("id") == message_id:
                        return message.get("result", {})

            await call("Page.navigate", {"url": url})
            deadline = time.monotonic() + PAGE_TIMEOUT
            while time.monotonic() < deadline:
                answer = await call("Runtime.evaluate", {"expression": "window.__testResult || null", "returnByValue": True})
                result = answer.get("result", {}).get("value")
                if result:
                    return result
                await asyncio.sleep(0.5)
            raise TimeoutError(f"No test result after {PAGE_TIMEOUT} s: {url}")
    finally:
        urllib.request.urlopen(f"{devtools}/json/close/{target['id']}").read()


def describe(failures):
    lines = []
    for failure in failures:
        lines.append(f"- {failure['test']}: {failure['message']}")
        if failure.get("expected") is not None or failure.get("actual") is not None:
            lines.append(f"    expected: {failure.get('expected')}\n    actual:   {failure.get('actual')}")
    return "\n".join(lines)


@pytest.mark.parametrize("page", PAGES)
def test_frontend(page, webapp_url, browser):
    result = asyncio.run(run_page(browser, webapp_url + PAGES[page]))
    print(f" {page}: {result['passed']}/{result['total']} assertions passed in {result['runtime'] / 1000:.1f} s")
    assert result["total"] > 0, "no test ran"
    assert result["failed"] == 0, f"{result['failed']} of {result['total']} assertions failed:\n" + describe(result["failures"])
