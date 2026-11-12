sap.ui.define([
	"./BaseController",
	"sap/m/MessageBox",
	"sap/m/MessageToast",
	"sap/ui/core/EventBus"
], (BaseController, MessageBox, MessageToast, EventBus) => {
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
			// Ignore the item press triggered by the delete button inside the item
			if (Date.now() - (this._deletePressedAt || 0) < 400) {
				return;
			}
			const sessionId = event.getSource().getBindingContext("app").getProperty("session_id");
			this.getRouter().navTo("conversation", { sessionId });
			this.onCloseSidebar();
		},

		onDeleteConversation(event) {
			this._deletePressedAt = Date.now();

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
