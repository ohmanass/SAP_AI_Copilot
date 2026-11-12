sap.ui.define([
	"./BaseController",
	"sap/ui/model/json/JSONModel"
], (BaseController, JSONModel) => {
	"use strict";

	return BaseController.extend("com.sapaicopilot.app.controller.Modules", {

		onInit() {
			this.getView().setModel(new JSONModel({ busy: false }), "view");
			this.getRouter().getRoute("modules").attachPatternMatched(this._onRouteMatched, this);
		},

		_onRouteMatched() {
			this.setSection("modules");
			this.onRefresh();
		},

		async onRefresh() {
			const viewModel = this.getView().getModel("view");
			viewModel.setProperty("/busy", true);
			try {
				await this.getOwnerComponent().refreshModules();
			} finally {
				viewModel.setProperty("/busy", false);
			}
		},

		onOpenModule(event) {
			const key = event.getSource().getBindingContext("app").getProperty("key");
			this.getRouter().navTo("moduleChat", { module: key });
		},

		formatTools(tools) {
			if (!tools || !tools.length) {
				return "—";
			}
			const bundle = this.getOwnerComponent().getModel("i18n").getResourceBundle();
			return tools.map((tool) => (bundle.hasText(`tool.${tool}`) ? bundle.getText(`tool.${tool}`) : tool)).join(" · ");
		},

		formatList(values) {
			return values && values.length ? values.join(", ") : "—";
		},

		formatDocuments(documents) {
			if (!documents || !documents.length) {
				return "—";
			}
			return documents.map((doc) => this.getText("moduleDocumentChunks", [doc.name, doc.chunks])).join(", ");
		}
	});
});
