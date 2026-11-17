"""
SAP module workspaces.

A module scopes a conversation: which tools the agent may call, which
documents the RAG searches, extra instructions for the system prompt and
the suggestions shown on the welcome screen.

Statuses describe what really exists, never more:
  connected      transactional tools + documentation
  documentation  RAG on module documents only
  planned        roadmap entry, not usable yet
Computed at runtime (system_info.get_modules):
  demo           connected module running on the local demo dataset
  unavailable    module that needs SAP S/4HANA while the demo dataset is configured
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
    # True when the module has no local demo data and needs SAP S/4HANA (SAP_DATA_SOURCE=odata)
    requires_s4hana: bool = False


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
            "transactional questions and the documentation for MM concepts. "
            "Customer sales orders belong to the SD workspace: if the user asks "
            "about them, do not call any tool, say so and suggest the SD workspace. "
            "Supplier invoices and the invoice match belong to the FI workspace: "
            "if the user asks about them, do not call any tool, say so and suggest "
            "the FI workspace."
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
            "asks for business data, do not call any tool, say so and suggest the "
            "matching workspace (MM for purchase orders, SD for sales orders, FI for "
            "supplier invoices)."
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
        description="Customer sales orders: customers, items, delivery status and late deliveries.",
        status="connected",
        icon="sap-icon://sales-order",
        tools=("get_sales_order", "search_sales_orders", "get_late_sales_orders", DOCUMENTATION_TOOL),
        doc_sources=("sap_sd.txt",),
        apis=("API_SALESORDER (OData V4)",),
        instructions=(
            "You are working in the SAP SD (Sales and Distribution) workspace. "
            "Focus on customer sales orders: customers, items, requested and "
            "confirmed delivery dates, delivery and processing status. "
            "Use the sales order tools for transactional questions and the "
            "documentation for SD concepts (tables, statuses, partner functions, "
            "order-to-cash process). Use both when the user asks to explain a "
            "sales order value, for example a status code. "
            "Purchase orders and suppliers belong to the MM workspace: if the user "
            "asks about them, do not call any tool, say so and suggest the MM workspace. "
            "Supplier invoices, payment blocks and the invoice match belong to the FI "
            "workspace: if the user asks about them, do not call any tool, say so and "
            "suggest the FI workspace."
        ),
        suggestions=(
            ("Show late sales orders.", "Late deliveries", "sap-icon://past"),
            ("Analyse sales order 538239.", "Sales order lookup", "sap-icon://sales-order"),
            ("Find the latest sales orders for US Large Cycles 2.", "Sales order search", "sap-icon://search"),
            ("Explain the difference between VBAK, VBAP and VBEP.", "Documentation", "sap-icon://course-book")
        ),
        requires_s4hana=True
    ),
    Module(
        key="fi",
        code="FI",
        name="Financial Accounting",
        description="Supplier invoices, payment blocks and the purchase order / goods receipt / invoice match.",
        status="connected",
        icon="sap-icon://money-bills",
        tools=("get_supplier_invoice", "search_supplier_invoices", "match_purchase_order_invoices",
               DOCUMENTATION_TOOL),
        doc_sources=("sap_fi.txt",),
        apis=("API_SUPPLIERINVOICE_PROCESS_SRV", "API_MATERIAL_DOCUMENT_SRV", "API_BUSINESS_PARTNER (OData V2)"),
        instructions=(
            "You are working in the SAP FI (Financial Accounting) workspace, focused on "
            "accounts payable: supplier invoices, payment blocks and the three-way match "
            "between purchase order, goods receipt and invoice. Use the invoice tools for "
            "transactional questions and the documentation for FI concepts (invoice "
            "verification, tolerances, GR/IR account, payment blocks). "
            "When an invoice is blocked or a match shows a variance, state the facts "
            "returned by the tools (quantities, amounts, documents) and never speculate "
            "about the business reason. "
            "Customer sales orders belong to the SD workspace: if the user asks about "
            "them, do not call any tool, say so and suggest the SD workspace."
        ),
        suggestions=(
            ("Show supplier invoices blocked for payment.", "Payment blocks", "sap-icon://locked"),
            ("Run the three-way match for purchase order 4500043954.", "Three-way match", "sap-icon://compare"),
            ("Analyse supplier invoice 5109900184.", "Invoice lookup", "sap-icon://receipt"),
            ("What is the GR/IR clearing account?", "Documentation", "sap-icon://course-book")
        ),
        requires_s4hana=True
    )
)

_BY_KEY = {module.key: module for module in MODULES}


def get_module(key):
    """Usable module for a key, or None (general workspace)."""
    module = _BY_KEY.get((key or "").strip().lower())
    if not module or module.status == "planned":
        return None
    if module.requires_s4hana:
        from app.sap_data import get_data_source
        if get_data_source().key != "odata":
            return None
    return module


def describe_modules(indexed_chunks_by_source: dict, added_documents: dict | None = None):
    """Module list for the frontend, with live documentation counts (shipped and added documents)."""

    result = []
    for module in MODULES:
        sources = list(module.doc_sources) + [
            name for name in (added_documents or {}).get(module.key, []) if name not in module.doc_sources
        ]
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
                for source in sources
            ],
            "indexed_chunks": sum(indexed_chunks_by_source.get(s, 0) for s in sources),
            "requires_s4hana": module.requires_s4hana,
            "suggestions": [
                {"text": text, "hint": hint, "icon": icon}
                for text, hint, icon in module.suggestions
            ]
        })
    return result
