sap.ui.define([
	"sap/ui/base/Object"
], (BaseObject) => {
	"use strict";

	/**
	 * Thin REST client for the SAP AI Copilot FastAPI backend.
	 * All endpoints are relative to the "copilotApi" data source of the manifest.
	 *
	 * Errors are normalised to an Error with:
	 *   kind:   "network" | "timeout" | "http"
	 *   status: HTTP status (http errors only)
	 *   detail: server-provided detail message, if any
	 */
	return BaseObject.extend("com.sapaicopilot.app.service.CopilotService", {

		// Must be a classic function: UI5 uses it with "new"
		constructor: function(settings) {
			BaseObject.call(this);
			this._baseUrl = settings.baseUrl.replace(/\/?$/, "/");
			this._requestTimeoutMs = settings.requestTimeoutMs;
			this._chatTimeoutMs = settings.chatTimeoutMs;
		},

		getBaseUrl() {
			return this._baseUrl;
		},

		health() {
			return this._request("health", { timeoutMs: 5000 });
		},

		getStatus(checkAi) {
			return this._request(checkAi ? "status?check_ai=true" : "status", {
				timeoutMs: checkAi ? 60000 : this._requestTimeoutMs
			});
		},

		chat(message, sessionId) {
			return this._request("chat", {
				method: "POST",
				body: { message, session_id: sessionId || null },
				timeoutMs: this._chatTimeoutMs
			});
		},

		listSessions() {
			return this._request("sessions");
		},

		getSession(sessionId) {
			return this._request(`sessions/${encodeURIComponent(sessionId)}`);
		},

		deleteSession(sessionId) {
			return this._request(`sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE" });
		},

		getKnowledgeBase() {
			return this._request("knowledge-base");
		},

		async _request(path, options = {}) {
			const controller = new AbortController();
			const timeoutMs = options.timeoutMs || this._requestTimeoutMs;
			const timer = setTimeout(() => controller.abort(), timeoutMs);

			const init = {
				method: options.method || "GET",
				headers: { Accept: "application/json" },
				signal: controller.signal
			};

			if (options.body !== undefined) {
				init.headers["Content-Type"] = "application/json";
				init.body = JSON.stringify(options.body);
			}

			let response;
			try {
				response = await fetch(this._baseUrl + path, init);
			} catch (cause) {
				throw this._error(cause.name === "AbortError" ? "timeout" : "network", cause.message);
			} finally {
				clearTimeout(timer);
			}

			const payload = await response.json().catch(() => null);

			if (!response.ok) {
				const detail = payload && typeof payload.detail === "string" ? payload.detail : "";
				const error = this._error("http", detail || response.statusText);
				error.status = response.status;
				error.detail = detail;
				throw error;
			}

			return payload;
		},

		_error(kind, message) {
			const error = new Error(message || kind);
			error.kind = kind;
			return error;
		}
	});
});
