sap.ui.define([
	"sap/ui/core/UIComponent",
	"sap/ui/model/json/JSONModel",
	"sap/ui/Device",
	"./service/CopilotService"
], (UIComponent, JSONModel, Device, CopilotService) => {
	"use strict";

	return UIComponent.extend("com.sapaicopilot.app.Component", {

		metadata: {
			manifest: "json",
			interfaces: ["sap.ui.core.IAsyncContentCreation"]
		},

		init() {
			UIComponent.prototype.init.apply(this, arguments);

			const config = this.getManifestEntry("/sap.ui5/config/copilot");
			const dataSource = this.getManifestEntry(`/sap.app/dataSources/${config.apiDataSource}`);

			this._config = config;
			this._service = new CopilotService({
				baseUrl: dataSource.uri,
				requestTimeoutMs: config.requestTimeoutMs,
				chatTimeoutMs: config.chatTimeoutMs
			});

			// Application-wide state shared by the shell and the pages
			this.setModel(new JSONModel({
				section: "chat",
				sidebarOpen: false,
				activeSessionId: null,
				backend: { state: "checking", checkedAt: null },
				model: { chat: "", provider: "", dataSource: "" },
				conversations: [],
				conversationsError: false,
				user: null
			}), "app");

			this._loadCurrentUser();

			this.setModel(new JSONModel(Device), "device");

			this.getRouter().initialize();

			this.checkBackend();
			this.refreshConversations();
			this._healthTimer = setInterval(() => this.checkBackend(), config.healthPollingIntervalMs);
		},

		getContentDensityClass() {
			return Device.support.touch && !Device.system.desktop ? "sapUiSizeCozy" : "sapUiSizeCompact";
		},

		getService() {
			return this._service;
		},

		async checkBackend() {
			const appModel = this.getModel("app");
			const wasOnline = appModel.getProperty("/backend/state") === "online";

			try {
				await this._service.health();
				appModel.setProperty("/backend/state", "online");

				// Model name comes from the backend configuration, never hard-coded
				if (!wasOnline || !appModel.getProperty("/model/chat")) {
					const status = await this._service.getStatus(false);
					appModel.setProperty("/model", {
						chat: status.ai.chat_model,
						provider: status.ai.provider,
						dataSource: status.sap_data ? status.sap_data.label : ""
					});
					this.refreshConversations();
				}
			} catch (error) {
				appModel.setProperty("/backend/state", "offline");
			}
			appModel.setProperty("/backend/checkedAt", new Date());
		},

		/**
		 * On SAP BTP the Application Router exposes the signed-in user.
		 * Locally the endpoint does not exist and the generic profile is kept.
		 */
		async _loadCurrentUser() {
			if (["localhost", "127.0.0.1"].includes(window.location.hostname)) {
				return; // local development: no approuter
			}
			try {
				const response = await fetch("user-api/currentUser", { headers: { Accept: "application/json" } });
				const type = response.headers.get("content-type") || "";
				if (!response.ok || !type.includes("application/json")) {
					return;
				}
				const user = await response.json();
				const name = [user.firstname, user.lastname].filter(Boolean).join(" ") || user.displayName || user.name;
				if (!name) {
					return;
				}
				const initials = name.split(/\s+/).map((part) => part[0]).join("").slice(0, 2).toUpperCase();
				this.getModel("app").setProperty("/user", { name, email: user.email || "", initials });
			} catch (error) {
				// Not behind the approuter: keep the local profile
			}
		},

		async refreshConversations() {
			const appModel = this.getModel("app");
			try {
				const result = await this._service.listSessions();
				appModel.setProperty("/conversations", result.sessions);
				appModel.setProperty("/conversationsError", false);
			} catch (error) {
				appModel.setProperty("/conversationsError", true);
			}
		},

		destroy() {
			clearInterval(this._healthTimer);
			if (this._service) {
				this._service.destroy();
			}
			UIComponent.prototype.destroy.apply(this, arguments);
		}
	});
});
