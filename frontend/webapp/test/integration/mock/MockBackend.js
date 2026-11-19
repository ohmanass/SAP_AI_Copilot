sap.ui.define([], () => {
	"use strict";

	/**
	 * Simulated backend for the OPA journeys: window.fetch answers the /api/* calls
	 * with the shapes of the real API (app/api.py), and streams the chat answer as
	 * NDJSON like /api/chat/stream. Nothing reaches a server, SAP or a model.
	 *
	 * Fictitious data only (this repository is public).
	 */

	const SESSION = "opa-session-0001";

	const ANSWER = {
		text: "There are **2** overdue purchase orders, the oldest one at **EV Parts** (28 days).",
		table: {
			kind: "overdue_purchase_orders", title_params: [], source: "SAP S/4HANA Cloud sandbox",
			attributes: [], total: 2, total_unit: "schedule_lines", window: "delivery dates from 2026-10-20 to 2026-11-19",
			row_action: "purchase_order", row_key: "po_number",
			columns: [
				{ key: "po_number", type: "id", importance: "high" },
				{ key: "supplier", type: "text", importance: "high" },
				{ key: "days_overdue", type: "days", importance: "high" },
				{ key: "status", type: "status", importance: "high" }
			],
			rows: [
				{ po_number: "4500000010", supplier: "EV Parts", days_overdue: 28, status: "OVERDUE" },
				{ po_number: "4500000011", supplier: "Bike Parts AG", days_overdue: 5, status: "OVERDUE" }
			],
			chart: {
				type: "bars", value_type: "days", series: ["days_overdue"], sorted: true, action: "purchase_order",
				shown: 2, total: 2,
				points: [
					{ label: "4500000010", detail: "EV Parts", key: "4500000010", values: [28], unit: null },
					{ label: "4500000011", detail: "Bike Parts AG", key: "4500000011", values: [5], unit: null }
				]
			}
		},
		title: "Overdue purchase orders of EV Parts",
		suggestions: ["Analyse purchase order 4500000010", "Check the invoices of EV Parts"]
	};

	const STATIC = {
		"GET health": { status: "online", application: "SAP AI Copilot" },
		"GET modules": {
			modules: [{
				key: "mm", code: "MM", name: "Materials Management", description: "Purchase orders and deliveries.",
				status: "connected", icon: "sap-icon://cart", tools: ["get_overdue_orders"], apis: [],
				documents: [{ name: "sap_mm.txt", chunks: 2 }], indexed_chunks: 2, requires_s4hana: false,
				data_source: "SAP S/4HANA Cloud sandbox",
				suggestions: [{ text: "Show overdue purchase orders.", hint: "Delivery analysis", icon: "sap-icon://shipping-status" }]
			}]
		},
		"GET dashboard": {
			days: 30, checked_at: "2026-11-19T10:00:00Z",
			indicators: [
				{ key: "overdue_purchase_orders", icon: "sap-icon://shipping-status", module: "mm", value: 3315, source: "S4", error: null },
				{ key: "late_sales_orders", icon: "sap-icon://past", module: "sd", value: 2810, source: "S4", error: null },
				{ key: "blocked_supplier_invoices", icon: "sap-icon://locked", module: "fi", value: null, source: null, error: "unavailable" }
			]
		},
		"GET status": {
			backend: { status: "online", application: "SAP AI Copilot", time: "2026-11-19T10:00:00+00:00" },
			ai: { provider: "SAP Generative AI Hub", configured: true, chat_model: "gpt-4o-mini",
				embedding_model: "text-embedding-3-large", connection: null },
			vector_store: { available: true, backend: "ChromaDB", collection: "sap_knowledge", total_chunks: 20,
				embedding_model: "text-embedding-3-large", error: null },
			sap_data: { key: "odata", label: "SAP S/4HANA Cloud sandbox", mode: "direct", destination: null,
				system: "https://sandbox.api.sap.com/s4hanacloud", api: "API_PURCHASEORDER_2 (OData V4)",
				configured: true, available: true, error: null, latency_ms: 420, purchase_orders: 1000 },
			chat_history: { backend: "SQLite", location: "chat_history.db", conversations: 1, available: true, error: null }
		},
		"GET usage": {
			days: 30, since: "2026-10-21", currency: null,
			total: { calls: 5, prompt_tokens: 6553, completion_tokens: 253, cost: null, total_tokens: 6806 },
			by_feature: [{ feature: "chat", calls: 2, prompt_tokens: 5498, completion_tokens: 205, cost: null, total_tokens: 5703 }],
			by_model: [{ model: "gpt-4o-mini", calls: 5, prompt_tokens: 6553, completion_tokens: 253, cost: null, total_tokens: 6806 }],
			by_day: [{ day: "2026-11-19", calls: 5, prompt_tokens: 6553, completion_tokens: 253, cost: null, total_tokens: 6806 }]
		},
		"GET i18n/languages": { source: "en", reviewed: ["en", "fr", "de", "es"], machine: ["it", "ja"] },
		"GET knowledge-base": {
			documents: [{
				name: "sap_mm.txt", type: "Text", extension: ".txt", size_bytes: 1562, modified_at: "2026-11-17T10:00:00Z",
				indexed_chunks: 2, expected_chunks: 2, status: "indexed", supported: true, origin: "bundled",
				module: "mm", can_delete: false, masked: {}
			}],
			orphan_sources: [],
			vector_store: { available: true, backend: "ChromaDB", collection: "sap_knowledge", total_chunks: 2, error: null },
			embedding_model: "text-embedding-3-large", documents_path: "data/documents",
			summary: { document_count: 1, indexed_documents: 1, uploaded_documents: 0, total_chunks: 2 }
		},
		"GET feedback": { scope: "all", stats: { total: 0, positive: 0, negative: 0, satisfaction: null }, items: [] }
	};

	let originalFetch = null;
	let state = null;

	const json = (body, status = 200) => new Response(JSON.stringify(body), {
		status, headers: { "Content-Type": "application/json" }
	});

	/** NDJSON body sent line by line, like the streaming answer of the backend. */
	function ndjson(events) {
		const encoder = new TextEncoder();
		return new Response(new ReadableStream({
			async start(controller) {
				for (const event of events) {
					await new Promise((resolve) => setTimeout(resolve, 30));
					controller.enqueue(encoder.encode(JSON.stringify(event) + "\n"));
				}
				controller.close();
			}
		}), { status: 200, headers: { "Content-Type": "application/x-ndjson" } });
	}

	function chatStream(body) {
		const sessionId = body.session_id || SESSION;
		const messageId = 100 + state.messages.length;
		const now = new Date().toISOString();
		state.questions.push(body.message);
		state.messages.push(
			{ role: "user", content: body.message, created_at: now, sources: [], tools_used: [], tables: [], attachments: [] },
			{ role: "assistant", content: ANSWER.text, created_at: now, sources: [], tools_used: ["get_overdue_orders"],
				tables: [ANSWER.table], attachments: [], id: messageId, feedback: null }
		);
		state.session = { session_id: sessionId, title: ANSWER.title, created_at: now, updated_at: now, module: null,
			message_count: state.messages.length };
		return ndjson([
			{ type: "session", session_id: sessionId, module: null },
			{ type: "tool_start", name: "get_overdue_orders" },
			{ type: "tool_end", name: "get_overdue_orders", success: true },
			{ type: "token", content: ANSWER.text },
			{ type: "done", response: ANSWER.text, sources: [], tools_used: ["get_overdue_orders"], tables: [ANSWER.table],
				session_id: sessionId, module: null, message_id: messageId },
			{ type: "title", session_id: sessionId, title: ANSWER.title },
			{ type: "suggestions", message_id: messageId, items: ANSWER.suggestions }
		]);
	}

	function answer(method, path, init) {
		const route = `${method} ${path.split("?")[0]}`;
		if (STATIC[route]) {
			return json(STATIC[route]);
		}
		if (route === "GET sessions") {
			return json({ sessions: state.session ? [state.session] : [] });
		}
		if (method === "GET" && path.startsWith("sessions/")) {
			return state.session
				? json(Object.assign({}, state.session, { messages: state.messages }))
				: json({ detail: "Conversation not found." }, 404);
		}
		if (route === "POST chat/stream") {
			return chatStream(JSON.parse(init.body));
		}
		return json({ detail: `Not simulated: ${route}` }, 404);
	}

	return {
		ANSWER,

		install() {
			state = { session: null, messages: [], questions: [] };
			if (originalFetch) {
				return;
			}
			originalFetch = window.fetch;
			window.fetch = (input, init = {}) => {
				const url = new URL(typeof input === "string" ? input : input.url, window.location.href);
				const api = /\/api\/(.+)$/.exec(url.pathname);
				if (api) {
					return Promise.resolve(answer((init.method || "GET").toUpperCase(), api[1] + url.search, init));
				}
				if (url.pathname.endsWith("/user-api/currentUser")) {
					return Promise.resolve(json({}, 404));
				}
				return originalFetch(input, init);
			};
		},

		uninstall() {
			if (originalFetch) {
				window.fetch = originalFetch;
				originalFetch = null;
			}
		},

		/** Questions sent to the assistant during the journey. */
		questions: () => state.questions.slice()
	};
});
