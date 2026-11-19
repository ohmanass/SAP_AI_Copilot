sap.ui.define([
	"./BaseController",
	"sap/ui/model/json/JSONModel",
	"sap/ui/core/EventBus",
	"sap/m/MessageToast",
	"sap/m/MessageBox",
	"../util/Markdown",
	"../util/DataTable",
	"../util/KnowledgeDialog",
	"../util/ConversationExport",
	"../util/Language",
	"../util/Dictation",
	"sap/m/Menu",
	"sap/m/MenuItem"
], (BaseController, JSONModel, EventBus, MessageToast, MessageBox, Markdown, DataTable, KnowledgeDialog,
	ConversationExport, Language, Dictation, Menu, MenuItem) => {
	"use strict";

	const TOOL_ICONS = {
		get_purchase_order: "sap-icon://sales-document",
		get_overdue_orders: "sap-icon://shipping-status",
		search_purchase_orders: "sap-icon://search",
		get_sales_order: "sap-icon://sales-order",
		search_sales_orders: "sap-icon://customer-order-entry",
		get_late_sales_orders: "sap-icon://past",
		get_supplier_invoice: "sap-icon://receipt",
		search_supplier_invoices: "sap-icon://money-bills",
		match_purchase_order_invoices: "sap-icon://compare",
		search_sap_documentation: "sap-icon://course-book",
		read_attached_document: "sap-icon://attachment-text-file"
	};

	// Attached documents: same limits as the backend (app/attachments/extract.py)
	const ATTACHMENT_EXTENSIONS = [
		".pdf", ".docx", ".xlsx", ".txt", ".md", ".csv", ".tsv", ".json", ".xml", ".log",
		".sql", ".abap", ".yaml", ".yml", ".html", ".htm",
		// Images: read by the AI model (app/attachments/vision.py)
		".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"
	];
	const ATTACHMENT_MAX_BYTES = 10 * 1024 * 1024;
	const ATTACHMENT_ICONS = {
		".pdf": "sap-icon://pdf-attachment",
		".docx": "sap-icon://doc-attachment",
		".xlsx": "sap-icon://excel-attachment",
		".csv": "sap-icon://excel-attachment",
		".tsv": "sap-icon://excel-attachment",
		".png": "sap-icon://attachment-photo",
		".jpg": "sap-icon://attachment-photo",
		".jpeg": "sap-icon://attachment-photo",
		".webp": "sap-icon://attachment-photo",
		".gif": "sap-icon://attachment-photo",
		".bmp": "sap-icon://attachment-photo",
		".tif": "sap-icon://attachment-photo",
		".tiff": "sap-icon://attachment-photo"
	};

	const extensionOf = (name) => {
		const match = /\.[^.]+$/.exec(name || "");
		return match ? match[0].toLowerCase() : "";
	};

	/** Random UUID (conversation created in the browser, local ids of uploads). */
	const newId = () => (window.crypto && window.crypto.randomUUID
		? window.crypto.randomUUID()
		: "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
			const r = Math.floor(Math.random() * 16);
			return (c === "x" ? r : (r % 4) + 8).toString(16);
		}));

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
				// Live SAP indicators of the welcome screen
				dashboard: [],
				// Dictation (util/Dictation.js)
				dictationSupported: Dictation.isSupported(),
				dictating: false,
				pending: false,
				activity: "",
				loading: false,
				draft: "",
				// Documents attached to the next message, and the id of the conversation
				// they belong to while it does not exist yet (new chat)
				attachments: [],
				uploading: false,
				draftSessionId: null,
				// Module workspace (null = general assistant)
				module: null,
				moduleInfo: null,
				suggestions: this._buildSuggestions()
			}), "chat");

			const router = this.getRouter();
			router.getRoute("chat").attachPatternMatched(this._onNewChatRoute, this);
			router.getRoute("conversation").attachPatternMatched(this._onConversationRoute, this);
			router.getRoute("moduleChat").attachPatternMatched(this._onModuleChatRoute, this);

			EventBus.getInstance().subscribe("copilot", "newChat", this._onNewChatEvent, this);
			EventBus.getInstance().subscribe("copilot", "conversationRenamed", this._onConversationRenamed, this);

			// Enter sends, Shift + Enter inserts a line break
			this.byId("input").attachBrowserEvent("keydown", this._onInputKeyDown, this);

			// Copy buttons of the code blocks in the answers (markup built by util/Markdown.js)
			Markdown.setLabels({ copyCode: this.getText("copyCode") });
			this.byId("scroller").attachBrowserEvent("click", this._onCodeCopyClick, this);

			// Keep the latest message in view whenever the thread is re-rendered
			const scrollDelegate = { onAfterRendering: () => this._scrollToBottom() };
			this.byId("thread").addEventDelegate(scrollDelegate);

			this._loadDashboard();
			this.byId("pendingIndicator").addEventDelegate(scrollDelegate);
		},

		onExit() {
			this._stopDictation();
			EventBus.getInstance().unsubscribe("copilot", "newChat", this._onNewChatEvent, this);
			EventBus.getInstance().unsubscribe("copilot", "conversationRenamed", this._onConversationRenamed, this);
		},

		/** Keep the header title in sync when the open conversation is renamed. */
		_onConversationRenamed(channel, event, data) {
			if (data && data.sessionId === this._chat().getProperty("/sessionId")) {
				this._chat().setProperty("/title", data.title);
			}
		},

		/* =========================== Routing =========================== */

		_onNewChatEvent() {
			this._startNewConversation(null);
			this._loadDashboard();
		},

		_onNewChatRoute() {
			this.setSection("chat");
			// A new, unsaved general chat (possibly still generating) is kept when coming
			// back from another page; "New chat" resets explicitly through the event bus.
			if (this._chat().getProperty("/sessionId") !== null || this._chat().getProperty("/module")) {
				this._startNewConversation(null);
			} else {
				this._focusInput();
			}
		},

		async _onModuleChatRoute(event) {
			this.setSection("modules");
			const key = event.getParameter("arguments").module;
			const chat = this._chat();

			// Coming back to the module workspace that is already open and unsaved
			if (chat.getProperty("/module") === key && chat.getProperty("/sessionId") === null) {
				this._focusInput();
				return;
			}

			const component = this.getOwnerComponent();
			const info = component.getModuleInfo(key) ||
				(await component.refreshModules(), component.getModuleInfo(key));

			if (!info || info.status === "planned" || info.status === "unavailable") {
				MessageToast.show(this.getText("modulesLoadError"));
				this.getRouter().navTo("modules", {}, true);
				return;
			}
			this._startNewConversation(key);
		},

		_onConversationRoute(event) {
			const sessionId = event.getParameter("arguments").sessionId;

			// Already displayed (e.g. right after the first answer of a new chat)
			if (this._chat().getProperty("/sessionId") === sessionId) {
				this.setSection(this._chat().getProperty("/module") ? "modules" : "chat");
				this.getModel("app").setProperty("/activeSessionId", sessionId);
				return;
			}
			this.setSection("chat");
			this._loadConversation(sessionId);
		},

		/** Scope the chat to a module workspace (or the general assistant when key is null). */
		_applyModule(key) {
			const chat = this._chat();
			const info = key ? this.getOwnerComponent().getModuleInfo(key) : null;
			chat.setProperty("/module", key || null);
			chat.setProperty("/moduleInfo", info);
			chat.setProperty("/suggestions", info && info.suggestions.length ? info.suggestions : this._buildSuggestions());
		},

		onLeaveModule() {
			EventBus.getInstance().publish("copilot", "newChat");
			this.getRouter().navTo("chat");
		},

		/** Leaves the conversation being displayed (a running request keeps going server-side). */
		_detachStream() {
			this._cancelReveal();
			this._stream = null;
			const chat = this._chat();
			chat.setProperty("/busy", false);
			chat.setProperty("/activity", "");
		},

		_startNewConversation(moduleKey) {
			this._conversationKey++;
			this._detachStream();
			const chat = this._chat();
			this._applyModule(moduleKey);
			this._resetAttachments();
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

			this._resetAttachments();
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
				if (conversation.module && !this.getOwnerComponent().getModuleInfo(conversation.module)) {
					await this.getOwnerComponent().refreshModules();
				}
				if (key !== this._conversationKey) {
					return;
				}
				this._applyModule(conversation.module);
				this.setSection(conversation.module ? "modules" : "chat");
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
			let text = (domRef ? domRef.value : input.getValue()).trim();
			const ready = this._readyAttachments().length;
			if (!text && ready) {
				text = this.getText(ready > 1 ? "attachmentDefaultPromptMany" : "attachmentDefaultPrompt");
			}
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
			this._send(failed.failedText, failed.failedAttachments);
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
		async _send(text, attachmentsOverride) {
			const chat = this._chat();
			this._stopDictation();

			if (!text || chat.getProperty("/busy") || chat.getProperty("/loading") || chat.getProperty("/uploading")) {
				return;
			}

			// Documents sent with this message (ready uploads, or the ones of a retried message)
			const attachments = attachmentsOverride || this._readyAttachments();

			const stream = {
				key: this._conversationKey,
				text,
				sessionId: chat.getProperty("/sessionId"),
				// Id sent to the backend: a new chat with documents already has one
				requestSessionId: chat.getProperty("/sessionId") || chat.getProperty("/draftSessionId"),
				module: chat.getProperty("/module"),
				index: -1,
				received: "",
				shown: 0,
				done: null
			};
			this._stream = stream;
			this._abortController = new AbortController();

			// Suggestions belong to the latest answer only
			chat.getProperty("/messages").forEach((message, i) => {
				if (message.suggestions && message.suggestions.length) {
					chat.setProperty(`/messages/${i}/suggestions`, []);
				}
			});
			this._append({ role: "user", content: text, attachments });
			chat.setProperty("/draft", "");
			chat.setProperty("/attachments", []);
			chat.setProperty("/busy", true);
			chat.setProperty("/pending", true);
			chat.setProperty("/activity", this.getText("thinking"));

			const onEvent = (event) => this._onStreamEvent(stream, event);

			try {
				try {
					await this.getService().chatStream(
						text, stream.requestSessionId, onEvent, this._abortController.signal, stream.module,
						attachments.map((a) => a.id)
					);
				} catch (error) {
					if (error.kind !== "http" || ![404, 405].includes(error.status)) {
						throw error;
					}
					// Older backend without /api/chat/stream
					const result = await this.getService().chat(
						text, stream.requestSessionId, stream.module, attachments.map((a) => a.id)
					);
					onEvent({ type: "token", content: result.response });
					onEvent(Object.assign({ type: "done" }, result));
					if (result.title) {
						onEvent({ type: "title", session_id: result.session_id, title: result.title });
					}
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
					this._append(this._errorMessage(this.describeError(error), text, attachments));
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
				case "suggestions":
					// Follow-up questions, after the answer
					stream.suggestions = event.items || [];
					if (stream.index >= 0) {
						const followBottom = this._isNearBottom();
						chat.setProperty(`/messages/${stream.index}/suggestions`, stream.suggestions);
						if (followBottom) {
							setTimeout(() => this._scrollToBottom(), 50);
						}
					}
					break;
				case "title":
					// New conversation: title written by the model, after the answer
					stream.title = event.title;
					if (chat.getProperty("/sessionId") === event.session_id) {
						chat.setProperty("/title", event.title);
					}
					this.getOwnerComponent().refreshConversations();
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
				tools_used: done.tools_used,
				tables: done.tables,
				id: done.message_id,
				suggestions: stream.suggestions || []
			}));

			if (!stream.sessionId) {
				chat.setProperty("/draftSessionId", null);
				chat.setProperty("/sessionId", done.session_id);
				chat.setProperty("/title", stream.title || this._buildTitle(stream.text));
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
			if (event.key === "Escape" && this._chat().getProperty("/dictating")) {
				this._stopDictation();
				return;
			}
			if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
				event.preventDefault();
				this.onSend();
			}
		},

		/* ======================= Dictation ======================= */

		/** Microphone button: starts or stops the dictation into the message field. */
		onDictate() {
			if (!this._dictation) {
				this._dictation = Dictation.create({
					language: () => Language.locale(),
					onText: (finalText, interim) => this._showDictatedText(finalText, interim),
					onState: (listening) => {
						this._chat().setProperty("/dictating", listening);
						if (!listening) {
							this._focusInput();
						}
					},
					onError: (code) => MessageToast.show(this.getText(
						{ "not-allowed": "dictationDenied", "service-not-allowed": "dictationDenied",
							"audio-capture": "dictationNoMicrophone", "no-speech": "dictationNoSpeech" }[code]
							|| "dictationUnavailable", [code]))
				});
			}
			if (!this._dictation.isListening()) {
				// The dictated text goes after what is already typed
				const draft = (this._chat().getProperty("/draft") || "").trimEnd();
				this._dictationBase = draft ? `${draft} ` : "";
			}
			this._dictation.toggle();
		},

		_showDictatedText(finalText, interim) {
			const text = `${finalText}${interim}`.replace(/\s+/g, " ").trimStart();
			this._chat().setProperty("/draft", (this._dictationBase || "") + text);
		},

		_stopDictation() {
			if (this._dictation && this._dictation.isListening()) {
				this._dictation.stop();
			}
		},

		/* ======================= Conversation export ======================= */

		/** Top bar button: Markdown file or PDF (browser print dialog). */
		onExportMenu(event) {
			if (!this._exportMenu) {
				this._exportMenu = new Menu({
					items: [
						new MenuItem({ text: this.getText("exportMarkdown"), icon: "sap-icon://attachment-text-file", key: "md" }),
						new MenuItem({ text: this.getText("exportPdf"), icon: "sap-icon://pdf-attachment", key: "pdf" })
					],
					itemSelected: (selected) => this._export(selected.getParameter("item").getKey())
				});
				this.getView().addDependent(this._exportMenu);
			}
			this._exportMenu.openBy(event.getSource());
		},

		_export(format) {
			const chat = this._chat();
			const moduleInfo = chat.getProperty("/moduleInfo");
			const conversation = {
				title: chat.getProperty("/title") || this.getText("newConversationTitle"),
				module: moduleInfo ? `${moduleInfo.code} · ${moduleInfo.name}` : "",
				model: this.getOwnerComponent().getModel("app").getProperty("/model/chat") || "",
				messages: chat.getProperty("/messages")
			};
			if (format === "pdf") {
				ConversationExport.print(this, conversation);
			} else {
				ConversationExport.downloadMarkdown(this, conversation);
				MessageToast.show(this.getText("exportDone"));
			}
		},

		/* ======================= Home dashboard ======================= */

		/** Live SAP indicators of the welcome screen (cached 5 minutes by the backend). */
		async _loadDashboard() {
			try {
				const data = await this.getService().getDashboard();
				this._chat().setProperty("/dashboard", data.indicators || []);
			} catch (error) {
				this._chat().setProperty("/dashboard", []); // the welcome screen works without it
			}
		},

		onDashboardPress(event) {
			const key = event.getSource().getBindingContext("chat").getProperty("key");
			this._send(this.getText(`dashboardPrompt.${key}`));
		},

		formatDashboardLabel(key) {
			return key ? this.getText(`dashboard.${key}`) : "";
		},

		formatDashboardValue(value) {
			return typeof value === "number" ? new Intl.NumberFormat(Language.locale()).format(value) : "—";
		},

		/* ======================= Follow-up suggestions ======================= */

		onSuggestionPress(event) {
			const text = event.getSource().getBindingContext("chat").getObject();
			if (typeof text === "string" && text) {
				this._send(text);
			}
		},

		/* ======================= Feedback on answers ======================= */

		onFeedbackUseful(event) {
			const context = event.getSource().getBindingContext("chat");
			this._rate(context, context.getProperty("rating") === 1 ? 0 : 1);
		},

		/** 👎: saved at once, then a comment can be added ("what was wrong?"). */
		async onFeedbackNotUseful(event) {
			const button = event.getSource();
			const context = button.getBindingContext("chat");
			if (context.getProperty("rating") === -1) {
				this._rate(context, 0);
				return;
			}
			if (await this._rate(context, -1)) {
				this._openFeedbackComment(button, context);
			}
		},

		/** Saves (1 / -1) or removes (0) the feedback; the button state changes at once. */
		async _rate(context, rating, comment) {
			const sessionId = this._chat().getProperty("/sessionId");
			const messageId = context.getProperty("id");
			const previous = context.getProperty("rating");
			const model = context.getModel();
			model.setProperty("rating", rating, context);
			try {
				if (rating) {
					const saved = await this.getService().setFeedback(sessionId, messageId, rating, comment || "");
					model.setProperty("feedbackComment", saved.comment, context);
					if (rating === 1 || comment) {
						MessageToast.show(this.getText("feedbackThanks"));
					}
				} else {
					await this.getService().deleteFeedback(sessionId, messageId);
					model.setProperty("feedbackComment", "", context);
				}
				return true;
			} catch (error) {
				model.setProperty("rating", previous, context);
				MessageToast.show(this.getText("feedbackFailed", [error.detail || this.describeError(error)]));
				return false;
			}
		},

		async _openFeedbackComment(button, context) {
			if (!this._feedbackPopover) {
				this._feedbackPopover = await this.loadFragment({
					name: "com.sapaicopilot.app.view.fragment.FeedbackComment"
				});
				this.getView().setModel(new JSONModel({ comment: "" }), "feedback");
			}
			this._feedbackContext = context;
			this.getView().getModel("feedback").setProperty("/comment", context.getProperty("feedbackComment") || "");
			this._feedbackPopover.openBy(button);
		},

		async onFeedbackCommentSend() {
			const comment = this.getView().getModel("feedback").getProperty("/comment").trim();
			this._feedbackPopover.close();
			if (comment && this._feedbackContext) {
				await this._rate(this._feedbackContext, -1, comment);
			}
		},

		onFeedbackCommentSkip() {
			this._feedbackPopover.close();
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

		async _onCodeCopyClick(event) {
			const button = event.target.closest && event.target.closest(".cpCopyCode");
			const block = button && button.closest(".cpCodeBlock");
			const code = block && block.querySelector("code");
			if (!code) {
				return;
			}
			try {
				await navigator.clipboard.writeText(code.textContent);
				button.classList.add("cpCopied");
				clearTimeout(button._copiedTimer);
				button._copiedTimer = setTimeout(() => button.classList.remove("cpCopied"), 1500);
				MessageToast.show(this.getText("codeCopied"));
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

		/** Factory of the SAP data tables shown under an answer. */
		createDataTable(id, context) {
			return DataTable.create(this, id, context);
		},

		/** A row of a list table asks the assistant for that document's details. */
		onDataRowPress(event, action, key) {
			this.askForDocument(action, event.getSource().getBindingContext("chat").getProperty(key));
		},

		/** A row or a chart bar asks the assistant for one document ("Analyse purchase order …"). */
		askForDocument(action, value) {
			if (value && !this._chat().getProperty("/busy")) {
				this._send(this.getText(`dataRowPrompt.${action}`, [value]));
			}
		},

		formatWelcomeText(moduleDescription, defaultText) {
			return moduleDescription ? this.getText("moduleWelcomeText", [moduleDescription]) : defaultText;
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
				tables: [],
				attachments: [],
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
				tables: raw.tables || [],
				attachments: raw.attachments || [],
				// Follow-up questions (only for the latest answer, never stored)
				suggestions: raw.suggestions || [],
				// Stored answer: id for the feedback, and the user's rating (1, -1 or 0)
				id: raw.id || null,
				rating: raw.feedback ? raw.feedback.rating : 0,
				feedbackComment: raw.feedback ? raw.feedback.comment : "",
				sources: (raw.sources || []).map((source) => Object.assign({}, source, {
					label: source.chunk === null || source.chunk === undefined
						? source.source
						: `${source.source} · ${this.getText("sourceChunk", [source.chunk])}`
				}))
			});
		},

		_errorMessage(text, failedText, failedAttachments) {
			return this._normalise({
				role: "error", content: text, failedText: failedText || "", failedAttachments: failedAttachments || []
			});
		},

		/* ======================= Attached documents ======================= */

		/** "+" button: opens the browser file picker. */
		onAttach() {
			const picker = document.createElement("input");
			picker.type = "file";
			picker.multiple = true;
			picker.accept = ATTACHMENT_EXTENSIONS.join(",");
			picker.addEventListener("change", () => this._uploadFiles(Array.from(picker.files || [])));
			picker.click();
		},

		async _uploadFiles(files) {
			const chat = this._chat();
			const key = this._conversationKey;
			let sessionId = chat.getProperty("/sessionId") || chat.getProperty("/draftSessionId");
			if (!sessionId) {
				sessionId = newId();
				chat.setProperty("/draftSessionId", sessionId);
			}

			const uploads = files.filter((file) => {
				if (!ATTACHMENT_EXTENSIONS.includes(extensionOf(file.name))) {
					MessageToast.show(this.getText("attachmentUnsupported", [file.name]));
					return false;
				}
				if (file.size > ATTACHMENT_MAX_BYTES) {
					MessageToast.show(this.getText("attachmentTooLarge", [file.name]));
					return false;
				}
				return true;
			}).map((file) => {
				const entry = { localId: newId(), name: file.name, uploading: true };
				chat.setProperty("/attachments", chat.getProperty("/attachments").concat(entry));
				return { file, entry };
			});
			this._updateUploading();

			await Promise.all(uploads.map(async ({ file, entry }) => {
				try {
					const summary = await this.getService().uploadAttachment(sessionId, file);
					if (key !== this._conversationKey) {
						// The user left this conversation meanwhile: do not keep an orphan document
						this.getService().deleteAttachment(sessionId, summary.id).catch(() => null);
						return;
					}
					this._replaceAttachment(entry.localId, Object.assign(summary, { localId: entry.localId, uploading: false }));
					const notes = [
						this.formatter.transcribedNote.call(this, summary.transcribed),
						this.formatter.maskedSummary.call(this, summary.masked)
					].filter(Boolean);
					if (notes.length) {
						MessageToast.show(`${summary.name}: ${notes.join(" ")}`, { duration: 6000 });
					}
				} catch (error) {
					if (key !== this._conversationKey) {
						return;
					}
					this._replaceAttachment(entry.localId, null);
					MessageToast.show(this.getText("attachmentFailed", [file.name, error.detail || this.describeError(error)]));
				}
			}));
			if (key === this._conversationKey) {
				this._updateUploading();
				this._focusInput();
			}
		},

		/** Adds a document sent in this conversation to the knowledge base of a workspace. */
		async onAttachmentToKnowledge(event) {
			const attachment = event.getSource().getBindingContext("chat").getObject();
			const sessionId = this._chat().getProperty("/sessionId");
			if (!sessionId || !attachment.id) {
				return;
			}
			await this.getOwnerComponent().refreshModules();
			const choice = await KnowledgeDialog.open(this, {
				documentName: attachment.name,
				module: this._chat().getProperty("/module") || ""
			});
			if (!choice) {
				return;
			}
			MessageToast.show(this.getText("kbAdding", [attachment.name]));
			try {
				const document = await this.getService().addKnowledgeFromAttachment(sessionId, attachment.id, choice.module);
				const masked = this.formatter.maskedSummary.call(this, document.masked);
				MessageToast.show(this.getText("kbAdded", [document.name, document.chunks]) + (masked ? ` ${masked}` : ""),
					{ duration: masked ? 6000 : 3000 });
				this.getOwnerComponent().refreshModules();
			} catch (error) {
				MessageBox.error(this.getText("kbAddFailed", [error.detail || this.describeError(error)]));
			}
		},

		onRemoveAttachment(event) {
			const attachment = event.getSource().getBindingContext("chat").getObject();
			this._replaceAttachment(attachment.localId, null);
			const chat = this._chat();
			const sessionId = chat.getProperty("/sessionId") || chat.getProperty("/draftSessionId");
			if (attachment.id && sessionId) {
				this.getService().deleteAttachment(sessionId, attachment.id).catch(() => null);
			}
			this._updateUploading();
		},

		_replaceAttachment(localId, replacement) {
			const chat = this._chat();
			const list = chat.getProperty("/attachments")
				.map((a) => (a.localId === localId ? replacement : a))
				.filter(Boolean);
			chat.setProperty("/attachments", list);
		},

		_readyAttachments() {
			return this._chat().getProperty("/attachments").filter((a) => !a.uploading && a.id);
		},

		_updateUploading() {
			const chat = this._chat();
			chat.setProperty("/uploading", chat.getProperty("/attachments").some((a) => a.uploading));
		},

		/** Leaving a conversation: documents not sent yet are discarded (also on the server). */
		_resetAttachments() {
			const chat = this._chat();
			const sessionId = chat.getProperty("/sessionId") || chat.getProperty("/draftSessionId");
			chat.getProperty("/attachments").forEach((a) => {
				if (a.id && sessionId) {
					this.getService().deleteAttachment(sessionId, a.id).catch(() => null);
				}
			});
			chat.setProperty("/attachments", []);
			chat.setProperty("/uploading", false);
			chat.setProperty("/draftSessionId", null);
		},

		formatAttachmentIcon(name) {
			return ATTACHMENT_ICONS[extensionOf(name)] || "sap-icon://document-text";
		},

		formatAttachmentMeta(pages, chars, masked) {
			const parts = [];
			const count = Object.values(masked || {}).reduce((sum, value) => sum + value, 0);
			if (pages) {
				parts.push(this.getText(pages === 1 ? "attachmentPage" : "attachmentPages", [pages]));
			}
			if (typeof chars === "number") {
				parts.push(this.getText("attachmentChars", [new Intl.NumberFormat(Language.locale()).format(chars)]));
			}
			if (count) {
				parts.push(this.getText(count === 1 ? "attachmentMaskedOne" : "attachmentMasked", [count]));
			}
			return parts.join(" · ");
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
