sap.ui.define([
	"./BaseController",
	"sap/ui/model/json/JSONModel"
], (BaseController, JSONModel) => {
	"use strict";

	return BaseController.extend("com.sapaicopilot.app.controller.KnowledgeBase", {

		onInit() {
			this.getView().setModel(new JSONModel({ busy: false, error: "", data: null }), "kb");
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
