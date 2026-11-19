/* global QUnit */
sap.ui.define([
	"com/sapaicopilot/app/util/ConversationExport",
	"sap/ui/model/resource/ResourceModel"
], (ConversationExport, ResourceModel) => {
	"use strict";

	/** Controller with the real English texts of the app (what the export needs of it). */
	function fakeController() {
		const model = new ResourceModel({ bundleName: "com.sapaicopilot.app.i18n.i18n", supportedLocales: [""], fallbackLocale: "" });
		const bundle = model.getResourceBundle();
		return {
			getText: (key, args) => bundle.getText(key, args),
			getOwnerComponent: () => ({ getModel: () => model })
		};
	}

	const conversation = {
		title: "Overdue purchase orders",
		module: "MM · Materials Management",
		model: "gpt-4o-mini",
		messages: [
			{ role: "user", content: "Show overdue POs\n# not a heading", time: "2026-11-19T10:00:00Z", attachments: [] },
			{
				role: "assistant", content: "**2** orders are late.", time: "2026-11-19T10:00:05Z",
				tools: [{ label: "Delivery analysis" }], sources: [], attachments: [],
				tables: [{
					kind: "overdue_purchase_orders", title_params: [], source: "SAP S/4HANA Cloud", window: null,
					attributes: [], total: null,
					columns: [{ key: "po_number", type: "id" }, { key: "supplier", type: "text" }, { key: "days_overdue", type: "days" }],
					rows: [{ po_number: "4500000010", supplier: "EV | Parts", days_overdue: 28 }]
				}]
			},
			{ role: "error", content: "The backend did not answer in time.", time: "2026-11-19T10:01:00Z" }
		]
	};

	QUnit.module("util/ConversationExport");

	QUnit.test("Markdown: questions quoted, answers, SAP tables and tools", (assert) => {
		const markdown = ConversationExport.toMarkdown(fakeController(), conversation);
		assert.ok(markdown.startsWith("# Overdue purchase orders"), "title");
		assert.ok(markdown.includes("Workspace: MM · Materials Management"), "workspace");
		assert.ok(markdown.includes("> Show overdue POs\n> # not a heading"), "the question is quoted: its Markdown stays text");
		assert.ok(markdown.includes("**2** orders are late."), "answer as written");
		assert.ok(markdown.includes("| Purchase order | Supplier | Overdue |"), "table header with column labels");
		assert.ok(markdown.includes("| 4500000010 | EV \\| Parts | 28 days |"), "cells escaped and formatted");
		assert.ok(markdown.includes("SAP tools used: Delivery analysis"), "tools");
		assert.notOk(markdown.includes("did not answer"), "errors are not exported");
	});

	QUnit.test("PDF page: every text escaped, no script", (assert) => {
		const page = ConversationExport.toHtml(fakeController(), Object.assign({}, conversation, {
			title: "<script>alert(1)</script>"
		}));
		const doc = new DOMParser().parseFromString(page, "text/html");
		assert.strictEqual(doc.querySelectorAll("script").length, 0);
		assert.strictEqual(doc.querySelector("h1").textContent, "<script>alert(1)</script>");
		assert.strictEqual(doc.querySelectorAll("section.msg").length, 2, "question and answer, not the error");
		assert.strictEqual(doc.querySelector("table td").textContent, "4500000010");
	});
});
