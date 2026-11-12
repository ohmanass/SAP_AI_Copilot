sap.ui.define([
	"./BaseController",
	"sap/ui/model/json/JSONModel",
	"sap/ui/core/EventBus",
	"sap/m/MessageToast",
	"../util/Markdown"
], (BaseController, JSONModel, EventBus, MessageToast, Markdown) => {
	"use strict";

	const TOOL_ICONS = {
		get_purchase_order: "sap-icon://sales-document",
		get_overdue_orders: "sap-icon://shipping-status",
		search_purchase_orders: "sap-icon://search",
		search_sap_documentation: "sap-icon://course-book"
	};

	const TITLE_MAX_LENGTH = 60;

	return BaseController.extend("com.sapaicopilot.app.controller.Chat", {

		onInit() {
			// Incremented whenever the displayed conversation changes, so that a late
			// answer for a conversation the user has left is not rendered in another one.
			this._conversationKey = 0;

			this.getView().setModel(new JSONModel({
				sessionId: null,
				title: this.getText("newConversationTitle"),
				messages: [],
				busy: false,
				pending: false,
				activity: "",
				loading: false,
				draft: "",
				suggestions: this._buildSuggestions()
			}), "chat");

			const router = this.getRouter();
			router.getRoute("chat").attachPatternMatched(this._onNewChatRoute, this);
			router.getRoute("conversation").attachPatternMatched(this._onConversationRoute, this);

			EventBus.getInstance().subscribe("copilot", "newChat", this._startNewConversation, this);

			// Enter sends, Shift + Enter inserts a line break
			this.byId("input").attachBrowserEvent("keydown", this._onInputKeyDown, this);

			// Keep the latest message in view whenever the thread is re-rendered
			const scrollDelegate = { onAfterRendering: () => this._scrollToBottom() };
			this.byId("thread").addEventDelegate(scrollDelegate);
			this.byId("pendingIndicator").addEventDelegate(scrollDelegate);
		},

		onExit() {
			EventBus.getInstance().unsubscribe("copilot", "newChat", this._startNewConversation, this);
		},

		/* =========================== Routing =========================== */

		_onNewChatRoute() {
			this.setSection("chat");
			// A new, unsaved chat (possibly still generating) is kept when coming back
			// from another page; "New chat" resets explicitly through the event bus.
			if (this._chat().getProperty("/sessionId") !== null) {
				this._startNewConversation();
			} else {
				this._focusInput();
			}
		},

		_onConversationRoute(event) {
			this.setSection("chat");
			const sessionId = event.getParameter("arguments").sessionId;

			// Already displayed (e.g. right after the first answer of a new chat)
			if (this._chat().getProperty("/sessionId") === sessionId) {
				this.getModel("app").setProperty("/activeSessionId", sessionId);
				return;
			}
			this._loadConversation(sessionId);
		},

		/** Leaves the conversation being displayed (a running request keeps going server-side). */
		_detachStream() {
			this._cancelReveal();
			this._stream = null;
			const chat = this._chat();
			chat.setProperty("/busy", false);
			chat.setProperty("/activity", "");
		},

		_startNewConversation() {
			this._conversationKey++;
			this._detachStream();
			const chat = this._chat();
			chat.setProperty("/sessionId", null);
			chat.setProperty("/title", this.getText("newConversationTitle"));
			chat.setProperty("/messages", []);
			chat.setProperty("/pending", false);
			chat.setProperty("/loading", false);
			this.getModel("app").setProperty("/activeSessionId", null);
			this._focusInput();
		},

		async _loadConversation(sessionId) {
			const key = ++this._conversationKey;
			this._detachStream();
			const chat = this._chat();

			chat.setProperty("/sessionId", sessionId);
			chat.setProperty("/title", "");
			chat.setProperty("/messages", []);
			chat.setProperty("/pending", false);
			chat.setProperty("/loading", true);
			this.getModel("app").setProperty("/activeSessionId", sessionId);

			try {
				const conversation = await this.getService().getSession(sessionId);
				if (key !== this._conversationKey) {
					return;
				}
				chat.setProperty("/title", conversation.title);
				chat.setProperty("/messages", conversation.messages.map((m) => this._toMessage(m)));
			} catch (error) {
				if (key !== this._conversationKey) {
					return;
				}
				if (error.status === 404) {
					MessageToast.show(this.getText("conversationNotFound"));
					this.getRouter().navTo("chat", {}, true);
					return;
				}
				chat.setProperty("/title", this.getText("newConversationTitle"));
				chat.setProperty("/messages", [this._errorMessage(
					`${this.getText("conversationLoadError")} ${this.describeError(error)}`
				)]);
			} finally {
				if (key === this._conversationKey) {
					chat.setProperty("/loading", false);
					this._focusInput();
				}
			}
		},

		/* =========================== Sending =========================== */

		onSend() {
			const input = this.byId("input");
			const domRef = input.getFocusDomRef();
			const text = (domRef ? domRef.value : input.getValue()).trim();
			this._send(text);
		},

		onSuggestionPress(event) {
			const text = event.getParameter("listItem").getBindingContext("chat").getProperty("text");
			this._send(text);
		},

		onRetry(event) {
			const failed = event.getSource().getBindingContext("chat").getObject();
			if (this._chat().getProperty("/busy")) {
				return;
			}
			const messages = this._chat().getProperty("/messages");
			const index = messages.indexOf(failed);

			// Remove the failed user message and its error, then send again
			this._chat().setProperty("/messages", messages.filter((m, i) => i !== index && i !== index - 1));
			this._send(failed.failedText);
		},

		onSendOrStop() {
			if (this._chat().getProperty("/busy")) {
				this.onStop();
			} else {
				this.onSend();
			}
		},

		onStop() {
			const stream = this._stream;
			if (!stream) {
				return;
			}
			if (stream.done) {
				// Answer already complete and saved: just finish the reveal now
				stream.shown = stream.received.length;
			} else if (this._abortController) {
				this._abortController.abort();
			}
		},

		/**
		 * Sends a message through the streaming endpoint (falls back to /api/chat
		 * when the backend does not provide it). Events update the UI live:
		 * tool activity, then the answer text revealed progressively.
		 */
		async _send(text) {
			const chat = this._chat();

			if (!text || chat.getProperty("/busy") || chat.getProperty("/loading")) {
				return;
			}

			const stream = {
				key: this._conversationKey,
				text,
				sessionId: chat.getProperty("/sessionId"),
				index: -1,
				received: "",
				shown: 0,
				done: null
			};
			this._stream = stream;
			this._abortController = new AbortController();

			this._append({ role: "user", content: text });
			chat.setProperty("/draft", "");
			chat.setProperty("/busy", true);
			chat.setProperty("/pending", true);
			chat.setProperty("/activity", this.getText("thinking"));

			const onEvent = (event) => this._onStreamEvent(stream, event);

			try {
				try {
					await this.getService().chatStream(text, stream.sessionId, onEvent, this._abortController.signal);
				} catch (error) {
					if (error.kind !== "http" || ![404, 405].includes(error.status)) {
						throw error;
					}
					// Older backend without /api/chat/stream
					const result = await this.getService().chat(text, stream.sessionId);
					onEvent({ type: "token", content: result.response });
					onEvent(Object.assign({ type: "done" }, result));
				}

				if (!stream.done) {
					throw Object.assign(new Error("Stream ended without an answer"), { kind: "stream" });
				}
			} catch (error) {
				if (stream.key !== this._conversationKey) {
					return;
				}
				this._cancelReveal();
				if (error.kind === "aborted") {
					this._markStopped(stream);
				} else {
					if (stream.index >= 0) {
						this._chat().setProperty(`/messages/${stream.index}/streaming`, false);
					}
					this._append(this._errorMessage(this.describeError(error), text));
					this._endBusy();
				}
			} finally {
				this._abortController = null;
				this.getOwnerComponent().refreshConversations();
			}
		},

		_onStreamEvent(stream, event) {
			if (stream.key !== this._conversationKey) {
				return; // the user switched conversation; the backend still saves the exchange
			}
			const chat = this._chat();

			switch (event.type) {
				case "tool_start":
					chat.setProperty("/activity", this._activityLabel(event.name));
					break;
				case "tool_end":
					// Keep the finished step readable: local tools complete in milliseconds
					chat.setProperty("/activity",
						`${this._activityLabel(event.name)} ${event.success ? "✓" : "✕"}  ·  ${this.getText("activityWriting")}`);
					break;
				case "token":
					stream.received += event.content;
					this._ensureStreamMessage(stream);
					this._startReveal(stream);
					break;
				case "done":
					stream.done = event;
					stream.received = event.response; // authoritative full text
					this._ensureStreamMessage(stream);
					this._startReveal(stream);
					break;
				default:
					break;
			}
		},

		_ensureStreamMessage(stream) {
			if (stream.index >= 0) {
				return;
			}
			const chat = this._chat();
			chat.setProperty("/pending", false);
			this._append({ role: "assistant", content: "", streaming: true });
			stream.index = chat.getProperty("/messages").length - 1;
		},

		/** Progressive reveal of text already received (Gen AI Hub delivers it in bursts). */
		_startReveal(stream) {
			if (this._revealFrame) {
				return;
			}
			const step = () => {
				if (stream.key !== this._conversationKey) {
					this._revealFrame = null;
					return;
				}
				const remaining = stream.received.length - stream.shown;
				if (remaining > 0) {
					stream.shown += Math.max(4, Math.ceil(remaining * 0.07));
					stream.shown = Math.min(stream.shown, stream.received.length);
					this._renderStream(stream);
				}
				if (stream.done && stream.shown >= stream.received.length) {
					this._revealFrame = null;
					this._finalize(stream);
					return;
				}
				this._revealFrame = requestAnimationFrame(step);
			};
			this._revealFrame = requestAnimationFrame(step);
		},

		_cancelReveal() {
			if (this._revealFrame) {
				cancelAnimationFrame(this._revealFrame);
				this._revealFrame = null;
			}
		},

		_renderStream(stream) {
			const followBottom = this._isNearBottom();
			const text = stream.received.slice(0, stream.shown);
			const path = `/messages/${stream.index}`;
			this._chat().setProperty(`${path}/content`, text);
			this._chat().setProperty(`${path}/html`, Markdown.render(text));
			if (followBottom) {
				this._scrollToBottom();
			}
		},

		_finalize(stream) {
			const chat = this._chat();
			const done = stream.done;
			const followBottom = this._isNearBottom();

			chat.setProperty(`/messages/${stream.index}`, this._toMessage({
				role: "assistant",
				content: done.response,
				sources: done.sources,
				tools_used: done.tools_used
			}));

			if (!stream.sessionId) {
				chat.setProperty("/sessionId", done.session_id);
				chat.setProperty("/title", this._buildTitle(stream.text));
				this.getModel("app").setProperty("/activeSessionId", done.session_id);
				this.getRouter().navTo("conversation", { sessionId: done.session_id }, true);
			}

			this._endBusy();
			this.getOwnerComponent().refreshConversations();
			if (followBottom) {
				this._scrollToBottom();
			}
		},

		_markStopped(stream) {
			const chat = this._chat();
			if (stream.index < 0) {
				this._ensureStreamMessage(stream);
			}
			const path = `/messages/${stream.index}`;
			const text = stream.received.slice(0, stream.shown);
			chat.setProperty(`${path}/content`, text);
			chat.setProperty(`${path}/html`, text ? Markdown.render(text) : "");
			chat.setProperty(`${path}/streaming`, false);
			chat.setProperty(`${path}/stopped`, true);
			this._endBusy();
		},

		_endBusy() {
			const chat = this._chat();
			chat.setProperty("/busy", false);
			chat.setProperty("/pending", false);
			chat.setProperty("/activity", "");
			this._stream = null;
			this._focusInput();
		},

		_activityLabel(toolName) {
			const key = `activity.${toolName}`;
			const bundle = this.getOwnerComponent().getModel("i18n").getResourceBundle();
			return bundle.hasText(key) ? bundle.getText(key) : this.getText("thinking");
		},

		_isNearBottom() {
			const scroller = this.byId("scroller");
			const domRef = scroller && scroller.getDomRef();
			return !domRef || domRef.scrollHeight - domRef.scrollTop - domRef.clientHeight < 120;
		},

		_onInputKeyDown(event) {
			if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
				event.preventDefault();
				this.onSend();
			}
		},

		/* ======================= Message actions ======================= */

		async onCopy(event) {
			const content = event.getSource().getBindingContext("chat").getProperty("content");
			try {
				await navigator.clipboard.writeText(content);
				MessageToast.show(this.getText("copied"));
			} catch (error) {
				MessageToast.show(this.getText("copyFailed"));
			}
		},

		async onSourcePress(event) {
			const button = event.getSource();
			if (!this._sourcePopover) {
				this._sourcePopover = this.loadFragment({
					name: "com.sapaicopilot.app.view.fragment.SourceDetail"
				});
			}
			const popover = await this._sourcePopover;
			popover.setBindingContext(button.getBindingContext("chat"), "chat");
			popover.openBy(button);
		},

		formatSourceChunk(chunk) {
			return chunk === null || chunk === undefined ? "" : this.getText("sourceChunk", [chunk]);
		},

		/* =========================== Helpers =========================== */

		_chat() {
			return this.getView().getModel("chat");
		},

		_append(message) {
			const messages = this._chat().getProperty("/messages");
			this._chat().setProperty("/messages", messages.concat(this._normalise(message)));
		},

		_normalise(message) {
			return Object.assign({
				tools: [],
				sources: [],
				html: "",
				streaming: false,
				stopped: false,
				time: new Date().toISOString()
			}, message);
		},

		_toMessage(raw) {
			const isAssistant = raw.role === "assistant";
			return this._normalise({
				role: raw.role,
				content: raw.content,
				html: isAssistant ? Markdown.render(raw.content) : "",
				time: raw.created_at || new Date().toISOString(),
				tools: (raw.tools_used || []).map((name) => ({
					name,
					label: this._toolLabel(name),
					icon: TOOL_ICONS[name] || "sap-icon://wrench"
				})),
				sources: (raw.sources || []).map((source) => Object.assign({}, source, {
					label: source.chunk === null || source.chunk === undefined
						? source.source
						: `${source.source} · ${this.getText("sourceChunk", [source.chunk])}`
				}))
			});
		},

		_errorMessage(text, failedText) {
			return this._normalise({ role: "error", content: text, failedText: failedText || "" });
		},

		_toolLabel(name) {
			const key = `tool.${name}`;
			const bundle = this.getOwnerComponent().getModel("i18n").getResourceBundle();
			return bundle.hasText(key) ? bundle.getText(key) : name;
		},

		_buildTitle(text) {
			const title = text.replace(/\s+/g, " ").trim();
			return title.length > TITLE_MAX_LENGTH ? `${title.slice(0, TITLE_MAX_LENGTH - 1).trim()}…` : title;
		},

		_buildSuggestions() {
			const icons = [
				"sap-icon://course-book",
				"sap-icon://shipping-status",
				"sap-icon://sales-document",
				"sap-icon://inventory"
			];
			return icons.map((icon, index) => ({
				icon,
				text: this.getText(`suggestion${index + 1}Title`),
				hint: this.getText(`suggestion${index + 1}Hint`)
			}));
		},

		_scrollToBottom() {
			requestAnimationFrame(() => {
				const scroller = this.byId("scroller");
				const domRef = scroller && scroller.getDomRef();
				if (domRef) {
					domRef.scrollTop = domRef.scrollHeight;
				}
			});
		},

		_focusInput() {
			setTimeout(() => {
				const input = this.byId("input");
				if (input && input.getDomRef()) {
					input.focus();
				}
			}, 0);
		}
	});
});
