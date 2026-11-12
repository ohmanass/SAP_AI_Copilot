sap.ui.define([
	"sap/ui/core/mvc/Controller",
	"sap/ui/core/UIComponent",
	"../model/formatter"
], (Controller, UIComponent, formatter) => {
	"use strict";

	return Controller.extend("com.sapaicopilot.app.controller.BaseController", {

		formatter,

		getRouter() {
			return UIComponent.getRouterFor(this);
		},

		getModel(name) {
			return this.getView().getModel(name) || this.getOwnerComponent().getModel(name);
		},

		getService() {
			return this.getOwnerComponent().getService();
		},

		getText(key, args) {
			return this.getOwnerComponent().getModel("i18n").getResourceBundle().getText(key, args);
		},

		/**
		 * Turn a CopilotService error into a user-facing message.
		 */
		describeError(error) {
			switch (error && error.kind) {
				case "network":
					return this.getText("errorNetwork", [this.getService().getBaseUrl()]);
				case "timeout":
					return this.getText("errorTimeout");
				case "http":
					return this.getText("errorServer", [error.status, error.detail || ""]).trim();
				default:
					return this.getText("errorGeneric");
			}
		},

		/** Mark the current sidebar section and close the mobile drawer. */
		setSection(section) {
			const appModel = this.getModel("app");
			appModel.setProperty("/section", section);
			appModel.setProperty("/sidebarOpen", false);
		},

		onOpenSidebar() {
			this.getModel("app").setProperty("/sidebarOpen", true);
		}
	});
});
