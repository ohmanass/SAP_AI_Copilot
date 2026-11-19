/* global QUnit */
sap.ui.define([
	"com/sapaicopilot/app/util/Language"
], (Language) => {
	"use strict";

	/** Browser languages for one test (navigator.languages is read-only: shadowed, then restored). */
	function withBrowserLanguages(languages, run) {
		Object.defineProperty(navigator, "languages", { get: () => languages, configurable: true });
		try {
			return run();
		} finally {
			delete navigator.languages;
		}
	}

	QUnit.module("util/Language");

	QUnit.test("Automatic: first browser language the app offers", (assert) => {
		const machine = ["ja", "pt-BR", "zh-CN"];
		const pick = (languages) => withBrowserLanguages(languages, () => Language.browserLanguage(machine));
		assert.strictEqual(pick(["fr-FR", "en"]), "fr", "reviewed language, by its base");
		assert.strictEqual(pick(["pt-br"]), "pt-BR", "exact tag of a regional variant");
		assert.strictEqual(pick(["pt-PT", "ja"]), "ja", "unknown variant skipped");
		assert.strictEqual(pick(["sw", "xx"]), "en", "nothing offered: English");
	});

	QUnit.test("The reviewed languages are the files of the app", (assert) => {
		assert.deepEqual(Language.REVIEWED, ["en", "fr", "de", "es"]);
	});

	QUnit.test("Each language is named in its own language", (assert) => {
		assert.strictEqual(Language.name("de", "de"), "Deutsch");
		assert.strictEqual(Language.name("ja", "ja"), "日本語");
		assert.strictEqual(Language.name("fr", "en"), "French");
		assert.ok(Language.label("de").startsWith("Deutsch"), "label: own name first");
	});
});
