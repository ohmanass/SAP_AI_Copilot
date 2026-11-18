sap.ui.define([
	"./BaseController",
	"sap/ui/model/json/JSONModel",
	"sap/ui/VersionInfo",
	"sap/ui/core/Theming",
	"sap/ui/core/Item",
	"sap/ui/core/SeparatorItem",
	"../util/Language"
], (BaseController, JSONModel, VersionInfo, Theming, Item, SeparatorItem, Language) => {
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

			this.getView().setModel(new JSONModel({ busy: false, error: "", days: "30", data: null }), "usage");

			const language = Language.current();
			this.getView().setModel(new JSONModel({
				selected: language.preference,
				options: [],
				busy: false,
				progress: "",
				// The chosen language could not be loaded at startup: English is shown
				error: language.error ? this.getText("languageFailed", [Language.name(language.preference, "en"), language.error]) : "",
				machine: language.machine
			}), "language");
			this._loadLanguages();

			this.getRouter().getRoute("settings").attachPatternMatched(this._onRouteMatched, this);
		},

		_onRouteMatched() {
			this.setSection("settings");
			this._load(false);
			this._loadUsage();
		},

		onRefresh() {
			this._load(false);
			this._loadUsage();
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

		/* ------------------------- Language ------------------------- */

		/** Options: automatic, reviewed translations, then languages translated by the model (by name). */
		async _loadLanguages() {
			const model = this.getView().getModel("language");
			const current = Language.current();
			const catalogue = await Language.catalogue();
			const reviewed = catalogue ? catalogue.reviewed : Language.REVIEWED;
			const machine = catalogue ? catalogue.machine : [];
			const byName = (codes) => codes
				.map((code) => ({ key: code, text: Language.label(code) }))
				.sort((a, b) => a.text.localeCompare(b.text, Language.locale()));

			const options = [
				{ key: Language.AUTO, text: this.getText("languageAuto", [Language.label(Language.browserLanguage(machine))]) },
				{ separator: true, text: this.getText("languageGroupReviewed") },
				...byName(reviewed)
			];
			if (machine.length) {
				options.push({ separator: true, text: this.getText("languageGroupMachine") }, ...byName(machine));
			}
			model.setProperty("/options", options);
			model.setProperty("/selected", current.preference);
		},

		createLanguageItem(id, context) {
			const option = context.getObject();
			return option.separator
				? new SeparatorItem(id, { text: option.text })
				: new Item(id, { key: option.key, text: option.text });
		},

		/** A language translated by the model is prepared first (once), then the app restarts in it. */
		async onLanguageChange(event) {
			const item = event.getParameter("selectedItem");
			const code = item.getKey();
			const name = item.getText();
			const model = this.getView().getModel("language");
			if (code === Language.current().preference) {
				return;
			}
			model.setProperty("/error", "");
			model.setProperty("/busy", true);
			try {
				if (code !== Language.AUTO && !Language.REVIEWED.includes(code)) {
					model.setProperty("/progress", this.getText("languagePreparing", [name]));
					await Language.prepare(code);
				}
				model.setProperty("/progress", this.getText("languageApplying", [name]));
				Language.choose(code);
			} catch (error) {
				model.setProperty("/progress", "");
				model.setProperty("/busy", false);
				model.setProperty("/selected", Language.current().preference);
				model.setProperty("/error", this.getText("languageFailed", [name, error.message || this.describeError(error)]));
			}
		},

		/* ------------------------- AI usage ------------------------- */

		onUsageDaysChange() {
			this._loadUsage();
		},

		async _loadUsage() {
			const model = this.getView().getModel("usage");
			model.setProperty("/busy", true);
			model.setProperty("/error", "");
			try {
				model.setProperty("/data", await this.getService().getUsage(Number(model.getProperty("/days"))));
			} catch (error) {
				model.setProperty("/data", null);
				model.setProperty("/error", `${this.getText("usageLoadError")} ${this.describeError(error)}`);
			} finally {
				model.setProperty("/busy", false);
			}
		},

		formatFeature(feature) {
			return feature ? this._optionalText(`usageFeature.${feature}`, feature) : "";
		},

		formatFeatureHint(feature) {
			return feature ? this._optionalText(`usageFeatureHint.${feature}`, "") : "";
		},

		_optionalText(key, fallback) {
			const bundle = this.getOwnerComponent().getModel("i18n").getResourceBundle();
			return bundle.hasText(key) ? bundle.getText(key) : fallback;
		},

		formatCost(cost, currency) {
			if (!currency) {
				return "—";
			}
			const value = typeof cost === "number" ? cost : 0;
			return new Intl.NumberFormat(Language.locale(), {
				style: "currency", currency, minimumFractionDigits: 2, maximumFractionDigits: value < 1 ? 4 : 2
			}).format(value);
		},

		/**
		 * Bar chart of the tokens per day, as inline SVG. Built from numbers and
		 * ISO dates only (validated below), so no text of the backend reaches the markup.
		 */
		formatUsageChart(days) {
			if (!Array.isArray(days) || !days.length) {
				return "<div></div>";
			}
			const width = 720;
			const height = 120;
			const gap = days.length > 40 ? 1 : 3;
			const barWidth = (width - gap * (days.length - 1)) / days.length;
			const max = Math.max(1, ...days.map((d) => Number(d.total_tokens) || 0));
			const number = new Intl.NumberFormat(Language.locale());
			const isoDay = (value) => (/^\d{4}-\d{2}-\d{2}$/.test(String(value)) ? String(value) : "");

			const bars = days.map((day, index) => {
				const tokens = Number(day.total_tokens) || 0;
				const calls = Number(day.calls) || 0;
				const barHeight = tokens ? Math.max(2, (tokens / max) * (height - 4)) : 0;
				const x = index * (barWidth + gap);
				const label = `${isoDay(day.day)} · ${number.format(tokens)} tokens · ${number.format(calls)} ${this.getText("usageCallsUnit")}`;
				return `<g><title>${label}</title>` +
					`<rect x="${x.toFixed(1)}" y="0" width="${barWidth.toFixed(1)}" height="${height}" class="cpUsageSlot"/>` +
					`<rect x="${x.toFixed(1)}" y="${(height - barHeight).toFixed(1)}" width="${barWidth.toFixed(1)}" ` +
					`height="${barHeight.toFixed(1)}" rx="2" class="cpUsageBar"/></g>`;
			}).join("");

			const first = isoDay(days[0].day);
			const last = isoDay(days[days.length - 1].day);
			return `<div class="cpUsageChart"><svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" ` +
				`role="img" aria-label="${this.getText("usageChartLabel")}">${bars}</svg>` +
				`<div class="cpUsageAxis"><span>${first}</span><span>${last}</span></div></div>`;
		},

		formatCount(value) {
			return typeof value === "number" ? new Intl.NumberFormat(Language.locale()).format(value) : "—";
		},

		formatLatency(value) {
			return typeof value === "number" ? this.getText("valueLatency", [value]) : "";
		},

		formatSapConnection(mode, destination) {
			return mode === "destination"
				? this.getText("connectionDestination", [destination])
				: this.getText("connectionDirect");
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
