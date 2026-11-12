"""
SAP module workspaces.

A module scopes a conversation: which tools the agent may call, which
documents the RAG searches, extra instructions for the system prompt and
the suggestions shown on the welcome screen.

Statuses describe what really exists, never more:
  connected      transactional tools + documentation
  documentation  RAG on module documents only
  planned        roadmap entry, not usable yet
"""

from dataclasses import dataclass, field

DOCUMENTATION_TOOL = "search_sap_documentation"


@dataclass(frozen=True)
class Module:
    key: str
    code: str
    name: str
    description: str
    status: str
    icon: str
    tools: tuple = ()
    doc_sources: tuple = ()
    apis: tuple = ()
    instructions: str = ""
    suggestions: tuple = field(default_factory=tuple)


MODULES = (
    Module(
        key="mm",
        code="MM",
        name="Materials Management",
        description="Procurement: purchase orders, suppliers, delivery schedules and goods receipt.",
        status="connected",
        icon="sap-icon://cart",
        tools=("get_purchase_order", "get_overdue_orders", "search_purchase_orders", DOCUMENTATION_TOOL),
        doc_sources=("sap_mm.txt",),
        apis=("API_PURCHASEORDER_2 (OData V4)",),
        instructions=(
            "You are working in the SAP MM (Materials Management) workspace. "
            "Focus on procurement: purchase orders, suppliers, delivery schedules, "
            "open quantities and goods receipt. Use the purchase order tools for "
            "transactional questions and the documentation for MM concepts."
        ),
        suggestions=(
            ("Show overdue purchase orders.", "Delivery analysis", "sap-icon://shipping-status"),
            ("Analyse purchase order 4500213237.", "Purchase order lookup", "sap-icon://sales-document"),
            ("Find the latest purchase orders from EV Parts.", "Purchase order search", "sap-icon://search"),
            ("Explain the difference between EKKO and EKPO.", "Documentation", "sap-icon://course-book")
        )
    ),
    Module(
        key="abap",
        code="ABAP",
        name="ABAP Development",
        description="CDS views, RAP OData services, SAP Fiori elements and ABAP SQL, from the indexed documentation.",
        status="documentation",
        icon="sap-icon://source-code",
        tools=(DOCUMENTATION_TOOL,),
        doc_sources=("sap_abap.txt",),
        instructions=(
            "You are working in the ABAP development workspace. Always search the "
            "documentation first and answer only from the retrieved ABAP documents. "
            "If they do not contain the answer, say clearly that the ABAP documentation "
            "does not cover this topic, do not answer from general knowledge, and suggest "
            "the relevant workspace (for example MM for purchasing tables such as EKKO). "
            "This workspace has no access to transactional SAP data: if the user "
            "asks for business data, say so and suggest the MM workspace."
        ),
        suggestions=(
            ("What are the different types of CDS views?", "CDS views", "sap-icon://database"),
            ("How is a CDS-based OData service exposed in RAP?", "RAP and OData", "sap-icon://chain-link"),
            ("What are the main SAP Fiori elements floorplans?", "SAP Fiori elements", "sap-icon://screen-split-three"),
            ("Show an ABAP SQL example that reads purchase orders.", "ABAP SQL", "sap-icon://source-code")
        )
    ),
    Module(
        key="sd",
        code="SD",
        name="Sales and Distribution",
        description="Sales orders, deliveries and billing. Planned: S/4HANA Sales Order API.",
        status="planned",
        icon="sap-icon://sales-order"
    ),
    Module(
        key="fi",
        code="FI",
        name="Financial Accounting",
        description="Supplier invoices and the purchase order / goods receipt / invoice match. Planned.",
        status="planned",
        icon="sap-icon://money-bills"
    )
)

_BY_KEY = {module.key: module for module in MODULES}


def get_module(key):
    """Usable module for a key, or None (general workspace)."""
    module = _BY_KEY.get((key or "").strip().lower())
    return module if module and module.status != "planned" else None


def describe_modules(indexed_chunks_by_source: dict):
    """Module list for the frontend, with live documentation counts."""

    result = []
    for module in MODULES:
        result.append({
            "key": module.key,
            "code": module.code,
            "name": module.name,
            "description": module.description,
            "status": module.status,
            "icon": module.icon,
            "tools": list(module.tools),
            "apis": list(module.apis),
            "documents": [
                {"name": source, "chunks": indexed_chunks_by_source.get(source, 0)}
                for source in module.doc_sources
            ],
            "indexed_chunks": sum(indexed_chunks_by_source.get(s, 0) for s in module.doc_sources),
            "suggestions": [
                {"text": text, "hint": hint, "icon": icon}
                for text, hint, icon in module.suggestions
            ]
        })
    return result
