/* global QUnit */
sap.ui.define([
	"com/sapaicopilot/app/util/Markdown"
], (Markdown) => {
	"use strict";

	const html = (markdown) => {
		const element = document.createElement("div");
		element.innerHTML = Markdown.render(markdown);
		return element;
	};

	QUnit.module("util/Markdown");

	QUnit.test("Every text is escaped: no markup of the model reaches the page", (assert) => {
		const element = html('<img src=x onerror="alert(1)"> **<b>bold</b>** `<script>`');
		assert.strictEqual(element.querySelector("img"), null, "no image tag");
		assert.strictEqual(element.querySelector("script"), null, "no script tag");
		assert.strictEqual(element.querySelector("strong").textContent, "<b>bold</b>", "markup shown as text");
		assert.ok(element.textContent.includes('<img src=x onerror="alert(1)">'), "raw text kept for reading");
	});

	QUnit.test("Links: http(s) only, opened in a new tab without opener", (assert) => {
		const element = html("[SAP](https://help.sap.com) and [bad](javascript:alert(1))");
		const links = element.querySelectorAll("a");
		assert.strictEqual(links.length, 1, "the javascript: link is not a link");
		assert.strictEqual(links[0].getAttribute("href"), "https://help.sap.com");
		assert.strictEqual(links[0].getAttribute("rel"), "noopener noreferrer");
		assert.strictEqual(links[0].getAttribute("target"), "_blank");
	});

	QUnit.test("Code blocks have their language and a Copy button", (assert) => {
		const element = html("Voici :\n\n```abap\nSELECT * FROM ekko INTO TABLE @DATA(lt).\n```");
		const block = element.querySelector(".cpCodeBlock");
		assert.ok(block, "code block");
		assert.strictEqual(block.querySelector(".cpCodeLang").textContent, "abap");
		assert.strictEqual(block.querySelector("code").textContent, "SELECT * FROM ekko INTO TABLE @DATA(lt).");
		const copy = block.querySelector("button.cpCopyCode");
		assert.ok(copy, "Copy button");
		assert.strictEqual(copy.getAttribute("type"), "button", "never submits a form");
	});

	QUnit.test("Tables, lists and headings", (assert) => {
		const element = html("### Orders\n\n| PO | Supplier |\n| --- | --- |\n| 4500000010 | EV Parts |\n\n- one\n- two\n\n1. first");
		assert.strictEqual(element.querySelector("h5").textContent, "Orders", "headings start at h3 in the bubble");
		assert.deepEqual([...element.querySelectorAll("td")].map((cell) => cell.textContent), ["4500000010", "EV Parts"]);
		assert.strictEqual(element.querySelectorAll("ul > li").length, 2);
		assert.strictEqual(element.querySelectorAll("ol > li").length, 1);
	});
});
