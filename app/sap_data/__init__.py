"""
SAP transactional data sources.

SAP_DATA_SOURCE in .env selects the backend used by the agent's tools:
  sqlite  local demo database with fictitious data (default, works offline)
  odata   SAP S/4HANA Cloud via OData V4 (sandbox by default)
"""

import os

from dotenv import load_dotenv

load_dotenv()

_source = None


def get_data_source():
    global _source

    if _source is None:
        if os.getenv("SAP_DATA_SOURCE", "sqlite").strip().lower() == "odata":
            from app.sap_data.odata_source import S4HanaODataSource
            _source = S4HanaODataSource()
        else:
            from app.sap_data.sqlite_source import SQLiteDemoSource
            _source = SQLiteDemoSource()

    return _source
