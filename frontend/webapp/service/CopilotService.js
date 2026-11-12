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

		/** Absolute API address, for display. */
		getBaseUrl() {
			return new URL(this._baseUrl, document.baseURI).href;
		},

		/**
		 * fetch() with the CSRF token required by the SAP Application Router for
		 * modifying requests. Locally (no approuter) no token is issued and the
		 * header is simply omitted.
		 */
		async _fetch(url, init) {
			const method = (init.method || "GET").toUpperCase();
			if (method === "GET" || method === "HEAD") {
				return fetch(url, init);
			}

			const send = async () => {
				const token = await this._csrfToken();
				const headers = Object.assign({}, init.headers, token ? { "X-CSRF-Token": token } : {});
				return fetch(url, Object.assign({}, init, { headers }));
			};

			let response = await send();
			if (response.status === 403 && (response.headers.get("x-csrf-token") || "").toLowerCase() === "required") {
				this._csrfPromise = null; // token expired with the session: fetch a new one once
				response = await send();
			}
			return response;
		},

		_csrfToken() {
			if (!this._csrfPromise) {
				this._csrfPromise = fetch(`${this._baseUrl}health`, { headers: { "X-CSRF-Token": "Fetch" } })
					.then((response) => response.headers.get("x-csrf-token"))
					.catch(() => null);
			}
			return this._csrfPromise;
		},

		/** The approuter answers with its login page (HTML) when the session has expired. */
		_checkSession(response) {
			const type = response.headers.get("content-type") || "";
			if (response.ok && type.includes("text/html")) {
				throw this._error("session", "Session expired");
			}
		},

		health() {
			return this._request("health", { timeoutMs: 5000 });
		},

		getStatus(checkAi) {
			return this._request(checkAi ? "status?check_ai=true" : "status", {
				timeoutMs: checkAi ? 60000 : this._requestTimeoutMs
			});
		},

		chat(message, sessionId, module) {
			return this._request("chat", {
				method: "POST",
				body: { message, session_id: sessionId || null, module: module || null },
				timeoutMs: this._chatTimeoutMs
			});
		},

		getModules() {
			return this._request("modules");
		},

		/**
		 * Streaming chat (NDJSON). Calls onEvent for every event received.
		 * Rejects with kind "aborted" when the caller aborts through `signal`,
		 * "timeout" when the backend stays silent too long, "stream" on a server-side error.
		 */
		async chatStream(message, sessionId, onEvent, signal, module) {
			const controller = new AbortController();
			let timedOut = false;
			let idleTimer;
			const resetIdleTimer = () => {
				clearTimeout(idleTimer);
				idleTimer = setTimeout(() => {
					timedOut = true;
					controller.abort();
				}, this._chatTimeoutMs);
			};
			const forwardAbort = () => controller.abort();
			if (signal) {
				signal.addEventListener("abort", forwardAbort);
			}

			try {
				resetIdleTimer();
				const response = await this._fetch(`${this._baseUrl}chat/stream`, {
					method: "POST",
					headers: { "Content-Type": "application/json", Accept: "application/x-ndjson" },
					body: JSON.stringify({ message, session_id: sessionId || null, module: module || null }),
					signal: controller.signal
				});

				this._checkSession(response);

				if (!response.ok) {
					const payload = await response.json().catch(() => null);
					const error = this._error("http", (payload && payload.detail) || response.statusText);
					error.status = response.status;
					error.detail = payload && typeof payload.detail === "string" ? payload.detail : "";
					throw error;
				}

				const reader = response.body.getReader();
				const decoder = new TextDecoder();
				let buffer = "";

				for (;;) {
					const { value, done } = await reader.read();
					if (done) {
						break;
					}
					resetIdleTimer();
					buffer += decoder.decode(value, { stream: true });

					let newline;
					while ((newline = buffer.indexOf("\n")) >= 0) {
						const line = buffer.slice(0, newline).trim();
						buffer = buffer.slice(newline + 1);
						if (!line) {
							continue;
						}
						const event = JSON.parse(line);
						if (event.type === "error") {
							throw this._error("stream", event.message);
						}
						onEvent(event);
					}
				}
			} catch (cause) {
				if (cause.kind) {
					throw cause;
				}
				if (cause.name === "AbortError") {
					throw this._error(timedOut ? "timeout" : "aborted", cause.message);
				}
				throw this._error("network", cause.message);
			} finally {
				clearTimeout(idleTimer);
				if (signal) {
					signal.removeEventListener("abort", forwardAbort);
				}
			}
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
				response = await this._fetch(this._baseUrl + path, init);
			} catch (cause) {
				throw this._error(cause.name === "AbortError" ? "timeout" : "network", cause.message);
			} finally {
				clearTimeout(timer);
			}

			this._checkSession(response);

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
