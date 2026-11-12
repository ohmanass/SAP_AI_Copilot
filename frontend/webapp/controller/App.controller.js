sap.ui.define([
	"./BaseController",
	"sap/m/MessageBox",
	"sap/m/MessageToast",
	"sap/ui/core/EventBus",
	"sap/ui/model/json/JSONModel"
], (BaseController, MessageBox, MessageToast, EventBus, JSONModel) => {
	"use strict";

	return BaseController.extend("com.sapaicopilot.app.controller.App", {

		onInit() {
			this.getView().addStyleClass(this.getOwnerComponent().getContentDensityClass());

			// Close the drawer when the backdrop is clicked (tablet / phone)
			this.getView().addEventDelegate({
				onclick: (event) => {
					if (event.target.classList.contains("cpBackdrop")) {
						this.onCloseSidebar();
					}
				}
			});
		},

		onCloseSidebar() {
			this.getModel("app").setProperty("/sidebarOpen", false);
		},

		onNewChat() {
			EventBus.getInstance().publish("copilot", "newChat");
			this.getRouter().navTo("chat");
			this.onCloseSidebar();
		},

		onNavSelect(event) {
			const key = event.getParameter("item").getKey();

			if (key === "chat") {
				const sessionId = this.getModel("app").getProperty("/activeSessionId");
				if (sessionId) {
					this.getRouter().navTo("conversation", { sessionId });
				} else {
					this.getRouter().navTo("chat");
				}
			} else {
				this.getRouter().navTo(key);
			}
			this.onCloseSidebar();
		},

		onConversationPress(event) {
			// Ignore the item press triggered by the rename / delete buttons inside the item
			if (Date.now() - (this._actionPressedAt || 0) < 400) {
				return;
			}
			const sessionId = event.getSource().getBindingContext("app").getProperty("session_id");
			this.getRouter().navTo("conversation", { sessionId });
			this.onCloseSidebar();
		},

		/* ------------------------- Rename ------------------------- */

		async onRenameConversation(event) {
			this._actionPressedAt = Date.now();
			const conversation = event.getSource().getBindingContext("app").getObject();

			if (!this._renameDialog) {
				this._renameDialog = this.loadFragment({
					name: "com.sapaicopilot.app.view.fragment.RenameDialog"
				});
				this.getView().setModel(new JSONModel({}), "rename");
			}
			const dialog = await this._renameDialog;

			this.getView().getModel("rename").setData({
				sessionId: conversation.session_id,
				title: conversation.title,
				valid: true,
				busy: false
			});
			dialog.open();
		},

		onRenameDialogOpened() {
			// Select the current title so typing replaces it
			const input = this.byId("renameInput");
			input.focus();
			const domRef = input.getFocusDomRef();
			if (domRef) {
				domRef.select();
			}
		},

		onRenameLiveChange(event) {
			this.getView().getModel("rename").setProperty("/valid", event.getParameter("value").trim().length > 0);
		},

		async onRenameSave() {
			const model = this.getView().getModel("rename");
			const { sessionId, title, busy } = model.getData();
			const newTitle = (title || "").trim();
			if (busy || !newTitle) {
				return;
			}

			model.setProperty("/busy", true);
			try {
				const result = await this.getService().renameSession(sessionId, newTitle);
				(await this._renameDialog).close();
				MessageToast.show(this.getText("conversationRenamed"));
				EventBus.getInstance().publish("copilot", "conversationRenamed", {
					sessionId,
					title: result.title
				});
				this.getOwnerComponent().refreshConversations();
			} catch (error) {
				MessageBox.error(this.describeError(error));
			} finally {
				model.setProperty("/busy", false);
			}
		},

		async onRenameCancel(promiseOrEvent) {
			// Escape key: UI5 passes {resolve, reject} and closes the dialog on resolve
			if (promiseOrEvent && typeof promiseOrEvent.resolve === "function") {
				promiseOrEvent.resolve();
				return;
			}
			(await this._renameDialog).close();
		},

		/* ------------------------- Delete ------------------------- */

		onDeleteConversation(event) {
			this._actionPressedAt = Date.now();

			const conversation = event.getSource().getBindingContext("app").getObject();
			const deleteAction = this.getText("deleteAction");

			MessageBox.warning(this.getText("deleteConversationText", [conversation.title]), {
				title: this.getText("deleteConversationTitle"),
				actions: [deleteAction, MessageBox.Action.CANCEL],
				emphasizedAction: deleteAction,
				onClose: (action) => {
					if (action === deleteAction) {
						this._deleteConversation(conversation.session_id);
					}
				}
			});
		},

		async _deleteConversation(sessionId) {
			const appModel = this.getModel("app");

			try {
				await this.getService().deleteSession(sessionId);
				MessageToast.show(this.getText("conversationDeleted"));

				if (appModel.getProperty("/activeSessionId") === sessionId) {
					EventBus.getInstance().publish("copilot", "newChat");
					this.getRouter().navTo("chat", {}, true);
				}
			} catch (error) {
				MessageBox.error(this.describeError(error));
			} finally {
				this.getOwnerComponent().refreshConversations();
			}
		}
	});
});
