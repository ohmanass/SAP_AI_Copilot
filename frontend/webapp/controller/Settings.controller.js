sap.ui.define([
	"./BaseController",
	"sap/ui/model/json/JSONModel",
	"sap/ui/VersionInfo",
	"sap/ui/core/Theming"
], (BaseController, JSONModel, VersionInfo, Theming) => {
	"use strict";

	return BaseController.extend("com.sapaicopilot.app.controller.Settings", {

		onInit() {
			const component = this.getOwnerComponent();

			this.getView().setModel(new JSONModel({
				busy: false,
				testing: false,
				error: "",
				status: null,
				checkedAt: null,
				endpoint: this.getService().getBaseUrl(),
				ui5Version: "",
				theme: Theming.getTheme(),
				appVersion: component.getManifestEntry("/sap.app/applicationVersion/version")
			}), "settings");

			VersionInfo.load({ library: "sap.ui.core" }).then((info) => {
				this.getView().getModel("settings").setProperty("/ui5Version", `SAPUI5 ${info.version}`);
			}).catch(() => {
				this.getView().getModel("settings").setProperty("/ui5Version", "—");
			});

			this.getRouter().getRoute("settings").attachPatternMatched(this._onRouteMatched, this);
		},

		_onRouteMatched() {
			this.setSection("settings");
			this._load(false);
		},

		onRefresh() {
			this._load(false);
			this.getOwnerComponent().checkBackend();
		},

		onTestConnection() {
			this._load(true);
		},

		async _load(checkAi) {
			const model = this.getView().getModel("settings");
			model.setProperty(checkAi ? "/testing" : "/busy", true);
			model.setProperty("/error", "");

			try {
				const status = await this.getService().getStatus(checkAi);

				// Keep the last connection test result when only refreshing
				const previous = model.getProperty("/status/ai/connection");
				if (!checkAi && previous) {
					status.ai.connection = previous;
				}
				model.setProperty("/status", status);
				this.getModel("app").setProperty("/backend/state", "online");
			} catch (error) {
				model.setProperty("/error", `${this.getText("statusLoadError")} ${this.describeError(error)}`);
				if (error.kind !== "http") {
					this.getModel("app").setProperty("/backend/state", "offline");
				}
			} finally {
				model.setProperty("/checkedAt", new Date());
				model.setProperty(checkAi ? "/testing" : "/busy", false);
			}
		},

		formatConnection(connection) {
			if (!connection) {
				return this.getText("valueNotTested");
			}
			return connection.reachable
				? this.getText("valueReachable", [connection.latency_ms])
				: this.getText("valueUnreachable", [connection.error]);
		}
	});
});
