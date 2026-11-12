"""
Cloud Foundry configuration bridge.

On SAP BTP, credentials come from bound services (VCAP_SERVICES) instead of
a .env file. This module maps them to the environment variables the rest of
the code already reads. It does nothing when running locally.

Expected user-provided services (created by deploy/deploy.ps1):
  sap-ai-copilot-aicore  {clientid, clientsecret, url, serviceurls.AI_API_URL, resource_group}
  sap-ai-copilot-s4      {api_key}
"""

import json
import os

AICORE_SERVICE = "sap-ai-copilot-aicore"
S4_SERVICE = "sap-ai-copilot-s4"


def _bound_credentials():
    try:
        services = json.loads(os.getenv("VCAP_SERVICES", "{}"))
    except ValueError:
        return {}

    return {
        instance.get("name"): instance.get("credentials", {})
        for instances in services.values()
        for instance in instances
    }


def _set_default(name, value):
    if value and not os.getenv(name):
        os.environ[name] = str(value)


def apply():
    credentials = _bound_credentials()

    aicore = credentials.get(AICORE_SERVICE)
    if aicore:
        _set_default("AICORE_CLIENT_ID", aicore.get("clientid"))
        _set_default("AICORE_CLIENT_SECRET", aicore.get("clientsecret"))
        _set_default("AICORE_AUTH_URL", aicore.get("url"))
        _set_default("AICORE_BASE_URL", (aicore.get("serviceurls") or {}).get("AI_API_URL"))
        _set_default("AICORE_RESOURCE_GROUP", aicore.get("resource_group"))

    s4 = credentials.get(S4_SERVICE)
    if s4:
        _set_default("SAP_API_KEY", s4.get("api_key"))


apply()
