/* global QUnit */
sap.ui.define([
	"com/sapaicopilot/app/util/DataChart"
], (DataChart) => {
	"use strict";

	const texts = {
		label: (key) => ({ gross_amount: "Gross amount", ordered_quantity: "Ordered", received_quantity: "Received",
			invoiced_quantity: "Invoiced" })[key] || key,
		text: (key, args) => `${key}(${(args || []).join(",")})`,
		value: (value, type, unit) => `${value} ${unit || type}`
	};

	const render = (chart, actionText = "Analyse") => {
		const element = document.createElement("div");
		element.innerHTML = DataChart.render(chart, texts, actionText);
		return element.firstElementChild;
	};

	const invoices = {
		type: "bars", value_type: "amount", series: ["gross_amount"], sorted: true, action: "supplier_invoice",
		shown: 2, total: 2,
		points: [
			{ label: "5100000001", detail: "Infinity Inc", key: "5100000001", values: [5000], unit: "USD" },
			{ label: "5100000002", detail: "<img src=x onerror=alert(1)>", key: "5100000002", values: [1250], unit: "USD" }
		]
	};

	QUnit.module("util/DataChart");

	QUnit.test("Bar widths are proportional to the largest value", (assert) => {
		const bars = [...render(invoices).querySelectorAll(".cpChartBar")];
		assert.deepEqual(bars.map((bar) => bar.style.width), ["100%", "25%"]);
	});

	QUnit.test("SAP texts are escaped", (assert) => {
		const figure = render(invoices);
		assert.strictEqual(figure.querySelector("img"), null, "no image injected by a supplier name");
		assert.strictEqual(figure.querySelectorAll(".cpChartDetail")[1].textContent, "<img src=x onerror=alert(1)>");
	});

	QUnit.test("A bar of an actionable table is a keyboard-reachable button", (assert) => {
		const row = render(invoices).querySelector(".cpChartRow");
		assert.strictEqual(row.getAttribute("role"), "button");
		assert.strictEqual(row.getAttribute("tabindex"), "0");
		assert.strictEqual(row.getAttribute("data-key"), "5100000001");
		assert.strictEqual(row.getAttribute("title"), "Analyse");
		assert.ok(row.getAttribute("aria-label").includes("5000 USD"), "described for screen readers");

		const passive = render(Object.assign({}, invoices, { action: null })).querySelector(".cpChartRow");
		assert.strictEqual(passive.getAttribute("role"), null, "no action: not a button");
	});

	QUnit.test("Three-way match: grouped bars with a legend", (assert) => {
		const figure = render({
			type: "grouped", value_type: "quantity", sorted: false, action: null, shown: 1, total: 1,
			series: ["ordered_quantity", "received_quantity", "invoiced_quantity"],
			points: [{ label: "10", detail: "Bike", key: null, values: [300, 295, 826], unit: "PC" }]
		});
		assert.strictEqual(figure.getAttribute("data-type"), "grouped");
		assert.deepEqual([...figure.querySelectorAll(".cpChartKey")].map((key) => key.textContent), ["Ordered", "Received", "Invoiced"]);
		assert.strictEqual(figure.querySelectorAll(".cpChartBar").length, 3);
		assert.strictEqual(figure.querySelectorAll(".cpChartBar")[2].style.width, "100%", "826 is the largest");
	});

	QUnit.test("A truncated chart says how many bars are shown", (assert) => {
		const figure = render(Object.assign({}, invoices, { shown: 2, total: 25 }));
		assert.strictEqual(figure.querySelector(".cpChartFoot").textContent, "chartShown(2,25)");
		assert.strictEqual(render(invoices).querySelector(".cpChartFoot"), null);
	});
});
