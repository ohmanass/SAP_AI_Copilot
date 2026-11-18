"""
Shared test setup.

The tests never call SAP, SAP AI Core or the Destination service: every
external answer is simulated. The SAP connection is reset to a known local
configuration so the developer's .env does not change the results.
"""

import os
import sys
from pathlib import Path

import pytest

# Importing the API must not index the knowledge base (it would call SAP AI Core)
os.environ["KB_SYNC_ON_STARTUP"] = "0"

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.sap_data import connection  # noqa: E402


@pytest.fixture(autouse=True)
def local_sap_connection(monkeypatch):
    monkeypatch.delenv("SAP_DESTINATION_NAME", raising=False)
    monkeypatch.delenv("VCAP_SERVICES", raising=False)
    monkeypatch.setenv("SAP_API_KEY", "test-key")
    monkeypatch.setenv("SAP_API_BASE_URL", "https://sandbox.api.sap.com/s4hanacloud")
    monkeypatch.setattr(connection, "_connection", None)
    yield
    connection._connection = None


@pytest.fixture(autouse=True)
def no_side_model_calls(monkeypatch):
    """Titles and follow-up suggestions never call the real model (tests replace it when they need one)."""
    from app.agent import followups, titles

    def offline(content):
        raise RuntimeError("no model in the offline tests")

    monkeypatch.setattr(titles, "_ask_model", offline)
    monkeypatch.setattr(followups, "_ask_model", offline)
