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
				pending: false,
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

		_startNewConversation() {
			this._conversationKey++;
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
			const messages = this._chat().getProperty("/messages");
			const index = messages.indexOf(failed);

			// Remove the failed user message and its error, then send again
			this._chat().setProperty("/messages", messages.filter((m, i) => i !== index && i !== index - 1));
			this._send(failed.failedText);
		},

		async _send(text) {
			const chat = this._chat();

			if (!text || chat.getProperty("/pending") || chat.getProperty("/loading")) {
				return;
			}

			const key = this._conversationKey;
			const sessionId = chat.getProperty("/sessionId");

			this._append({ role: "user", content: text });
			chat.setProperty("/draft", "");
			chat.setProperty("/pending", true);

			try {
				const result = await this.getService().chat(text, sessionId);

				if (key !== this._conversationKey) {
					// The user moved to another conversation; the backend saved it anyway
					return;
				}

				this._append(this._toMessage({
					role: "assistant",
					content: result.response,
					sources: result.sources,
					tools_used: result.tools_used
				}));

				if (!sessionId) {
					chat.setProperty("/sessionId", result.session_id);
					chat.setProperty("/title", this._buildTitle(text));
					this.getModel("app").setProperty("/activeSessionId", result.session_id);
					this.getRouter().navTo("conversation", { sessionId: result.session_id }, true);
				}
			} catch (error) {
				if (key === this._conversationKey) {
					this._append(this._errorMessage(this.describeError(error), text));
				}
			} finally {
				if (key === this._conversationKey) {
					chat.setProperty("/pending", false);
					this._focusInput();
				}
				this.getOwnerComponent().refreshConversations();
			}
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
			return Object.assign({ tools: [], sources: [], html: "", time: new Date().toISOString() }, message);
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
