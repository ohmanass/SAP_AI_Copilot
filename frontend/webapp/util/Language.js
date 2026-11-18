sap.ui.define([
	"sap/base/i18n/Localization",
	"sap/base/i18n/ResourceBundle",
	"sap/base/Log"
], (Localization, ResourceBundle, Log) => {
	"use strict";

	/**
	 * Interface language.
	 *
	 * - Reviewed languages: i18n/i18n_<code>.properties, shipped with the app and
	 *   loaded by the "i18n" ResourceModel of the manifest.
	 * - Other languages: translated once by the AI model on the backend
	 *   (GET api/i18n/<code>.properties, see app/i18n/translator.py); the bundle is
	 *   loaded before the component is created and replaces the "i18n" model.
	 *
	 * The choice ("auto" = browser language) is kept in the browser. It is applied
	 * once at startup (boot.js), before any text is rendered: changing it reloads
	 * the app, so every control, date and number is created in the new language.
	 */

	const STORAGE_KEY = "sap-ai-copilot.language";
	const AUTO = "auto";
	const SOURCE = "en";
	// Must match the i18n_<code>.properties files (checked by tests/test_i18n.py)
	const REVIEWED = ["en", "fr", "de", "es"];
	const API = "api/i18n/";

	let state = { preference: AUTO, code: SOURCE, machine: false, bundle: null, error: null };

	function readPreference() {
		try {
			return window.localStorage.getItem(STORAGE_KEY) || AUTO;
		} catch (error) {
			return AUTO; // storage disabled: browser language
		}
	}

	function writePreference(code) {
		try {
			window.localStorage.setItem(STORAGE_KEY, code);
		} catch (error) {
			Log.warning("Language choice not saved: browser storage unavailable", "", "com.sapaicopilot.app.Language");
		}
	}

	/** Languages offered by the backend ({reviewed, machine}), or null when it is not reachable. */
	async function fetchCatalogue() {
		try {
			const response = await fetch(`${API}languages`, { credentials: "same-origin" });
			return response.ok ? await response.json() : null;
		} catch (error) {
			return null;
		}
	}

	/** First browser language the app offers: exact tag (pt-BR, zh-CN), then base language. */
	function browserLanguage(machineCodes) {
		const offered = new Set([...REVIEWED, ...machineCodes]);
		for (const tag of navigator.languages || [navigator.language]) {
			if (!tag) {
				continue;
			}
			const [base, region] = tag.split("-");
			const exact = region ? `${base.toLowerCase()}-${region.toUpperCase()}` : base.toLowerCase();
			if (offered.has(exact)) {
				return exact;
			}
			if (offered.has(base.toLowerCase())) {
				return base.toLowerCase();
			}
		}
		return SOURCE;
	}

	/** Downloads the translation by the AI model (translated by the backend on first use). */
	async function loadMachineBundle(code) {
		const url = `${API}${encodeURIComponent(code)}.properties`;
		// Explicit request first: ResourceBundle would silently show the keys if the backend failed
		const response = await fetch(url, { credentials: "same-origin" });
		if (!response.ok) {
			let detail = `HTTP ${response.status}`;
			try {
				detail = (await response.json()).detail || detail;
			} catch (error) {
				// not JSON: keep the status
			}
			throw new Error(detail);
		}
		return ResourceBundle.create({ url, async: true, supportedLocales: [""], fallbackLocale: "" });
	}

	return {

		AUTO,
		REVIEWED,

		/**
		 * Startup (boot.js): sets the UI5 language and, for a language translated by
		 * the AI model, preloads its bundle. Falls back to English on any failure.
		 */
		async apply() {
			const preference = readPreference();
			let code = preference;
			let catalogue = null;

			if (preference === AUTO || !REVIEWED.includes(preference)) {
				catalogue = await fetchCatalogue();
			}
			const machineCodes = catalogue ? catalogue.machine : [];
			if (preference === AUTO) {
				code = browserLanguage(machineCodes);
			} else if (!REVIEWED.includes(preference) && catalogue && !machineCodes.includes(preference)) {
				code = SOURCE; // no longer offered
			}

			state = { preference, code, machine: !REVIEWED.includes(code), bundle: null, error: null };
			if (state.machine) {
				try {
					state.bundle = await loadMachineBundle(code);
				} catch (error) {
					Log.error(`Interface translation into ${code} unavailable`, error.message, "com.sapaicopilot.app.Language");
					state = { preference, code: SOURCE, machine: false, bundle: null, error: error.message };
				}
			}

			Localization.setLanguage(state.code);
			document.documentElement.lang = state.code;
			document.documentElement.dir = Localization.getRTL() ? "rtl" : "ltr";
		},

		/** Bundle translated by the AI model for the current language, or null (reviewed language). */
		machineBundle() {
			return state.bundle;
		},

		/** Current choice ("auto" or a code), language shown, and whether it is translated by the model. */
		current() {
			return { preference: state.preference, code: state.code, machine: state.machine, error: state.error };
		},

		catalogue: fetchCatalogue,

		/** Language "automatic" stands for: the first browser language the app offers. */
		browserLanguage,

		/** Translates the interface now (first use of a language): resolves when it is ready. */
		async prepare(code) {
			if (!REVIEWED.includes(code)) {
				await loadMachineBundle(code);
			}
		},

		/** Saves the choice and restarts the app in that language. */
		choose(code) {
			writePreference(code);
			window.location.reload();
		},

		/** Name of a language in that language ("Deutsch", "日本語"), with its name in the current language. */
		label(code) {
			const native = this.name(code, code);
			const local = this.name(code, state.code);
			return native.toLocaleLowerCase(code) === local.toLocaleLowerCase(state.code) ? native : `${native} · ${local}`;
		},

		name(code, inLanguage) {
			try {
				const name = new Intl.DisplayNames([inLanguage], { type: "language" }).of(code) || code;
				return name.charAt(0).toLocaleUpperCase(inLanguage) + name.slice(1);
			} catch (error) {
				return code;
			}
		},

		/** BCP 47 tag of the interface language, for Intl formatting. */
		locale() {
			return Localization.getLanguageTag().toString();
		}
	};
});
