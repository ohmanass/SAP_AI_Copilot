sap.ui.define([
	"./BaseController",
	"sap/ui/model/json/JSONModel",
	"sap/m/MessageBox",
	"sap/m/MessageToast",
	"../util/KnowledgeDialog"
], (BaseController, JSONModel, MessageBox, MessageToast, KnowledgeDialog) => {
	"use strict";

	return BaseController.extend("com.sapaicopilot.app.controller.KnowledgeBase", {

		onInit() {
			this.getView().setModel(new JSONModel({ busy: false, adding: false, error: "", data: null }), "kb");
			this.getRouter().getRoute("knowledgeBase").attachPatternMatched(this._onRouteMatched, this);
		},

		_onRouteMatched() {
			this.setSection("knowledgeBase");
			this._load();
		},

		onRefresh() {
			this._load();
		},

		async _load() {
			const model = this.getView().getModel("kb");
			model.setProperty("/busy", true);
			model.setProperty("/error", "");

			try {
				model.setProperty("/data", await this.getService().getKnowledgeBase());
			} catch (error) {
				model.setProperty("/data", null);
				model.setProperty("/error", `${this.getText("kbLoadError")} ${this.describeError(error)}`);
			} finally {
				model.setProperty("/busy", false);
			}
		},

		/** Upload a document: text extracted, chunked and embedded by the backend. */
		async onAddDocument() {
			await this.getOwnerComponent().refreshModules();
			const choice = await KnowledgeDialog.open(this);
			if (!choice || !choice.file) {
				return;
			}
			const model = this.getView().getModel("kb");
			model.setProperty("/adding", true);
			try {
				const document = await this.getService().addKnowledgeDocument(choice.file, choice.module);
				MessageToast.show(this.getText("kbAdded", [document.name, document.chunks]));
				this.getOwnerComponent().refreshModules();
				await this._load();
			} catch (error) {
				MessageBox.error(this.getText("kbAddFailed", [error.detail || this.describeError(error)]));
			} finally {
				model.setProperty("/adding", false);
			}
		},

		onDeleteDocument(event) {
			const document = event.getSource().getBindingContext("kb").getObject();
			const deleteAction = this.getText("deleteAction");
			MessageBox.warning(this.getText("kbDeleteText", [document.name]), {
				title: this.getText("kbDeleteTitle"),
				actions: [deleteAction, MessageBox.Action.CANCEL],
				emphasizedAction: deleteAction,
				onClose: async (action) => {
					if (action !== deleteAction) {
						return;
					}
					try {
						await this.getService().deleteKnowledgeDocument(document.name);
						MessageToast.show(this.getText("kbDeleted", [document.name]));
						this.getOwnerComponent().refreshModules();
						await this._load();
					} catch (error) {
						MessageBox.error(error.detail || this.describeError(error));
					}
				}
			});
		},

		formatModule(module) {
			if (!module) {
				return this.getText("kbModuleAll");
			}
			const info = this.getOwnerComponent().getModuleInfo(module);
			return info ? info.code : module.toUpperCase();
		},

		formatDocumentLocation(origin, path, name) {
			return origin === "uploaded" ? this.getText("kbOriginUploaded") : `${path}/${name}`;
		},

		formatVectorStoreError(error) {
			return this.getText("kbVectorStoreError", [error || "?"]);
		},

		hasItems(list) {
			return Array.isArray(list) && list.length > 0;
		},

		formatOrphans(orphans) {
			if (!orphans || !orphans.length) {
				return "";
			}
			return this.getText("kbOrphans", [orphans.length, orphans.map((o) => o.name).join(", ")]);
		}
	});
});
