/* global QUnit */
sap.ui.define([
	"com/sapaicopilot/app/model/formatter"
], (formatter) => {
	"use strict";

	// Formatters run with the controller as "this": only getText is needed
	const controller = { getText: (key, args) => `${key}${args ? `(${args.join(",")})` : ""}` };
	const call = (name, ...args) => formatter[name].apply(controller, args);

	QUnit.module("model/formatter");

	QUnit.test("Masked sensitive values: singular, plural, nothing", (assert) => {
		assert.strictEqual(call("maskedSummary", { credential: 2, iban: 1 }),
			"maskedSummary(maskedKinds.credential(2), maskedKind.iban(1))");
		assert.strictEqual(call("maskedSummary", { credential: 0 }), "");
		assert.strictEqual(call("maskedSummary", null), "");
	});

	QUnit.test("Images read by the model", (assert) => {
		assert.strictEqual(call("transcribedNote", 1), "transcribedOne");
		assert.strictEqual(call("transcribedNote", 3), "transcribedPages(3)");
		assert.strictEqual(call("transcribedNote", 0), "");
	});

	QUnit.test("Knowledge base chunks and module status", (assert) => {
		assert.strictEqual(call("moduleStatusState", "connected"), "Success");
		assert.ok(call("kbChunks", 12, 12).includes("12"));
		assert.strictEqual(call("upper", "mm"), "MM");
		assert.strictEqual(call("upper", null), "");
	});
});
