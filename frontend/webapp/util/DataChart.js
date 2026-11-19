sap.ui.define([
	"./Markdown"
], (Markdown) => {
	"use strict";

	/**
	 * Chart of a SAP data table (built by the backend, app/agent/charts.py): horizontal
	 * bars, readable on a phone, one bar per document or item, or grouped bars for
	 * the quantities of a three-way match.
	 *
	 * render() returns the markup only (no event, no state). Every text that comes
	 * from SAP is escaped; widths are numbers computed here.
	 */

	const esc = Markdown.escapeHtml;

	/**
	 * @param {object} chart  chart of the table (type, value_type, series, points, action...)
	 * @param {object} texts  helpers: label(columnKey), text(key, args), value(number, valueType, unit)
	 * @param {string} actionText  tooltip of an actionable bar ("Select a row to analyse…"), or ""
	 */
	function render(chart, texts, actionText) {
		const max = Math.max(...chart.points.flatMap((point) => point.values.map(Math.abs)), 0) || 1;
		const seriesNames = chart.series.map((key) => texts.label(key));
		const grouped = chart.type === "grouped";
		const actionable = !!chart.action;

		const legend = grouped
			? `<span class="cpChartLegend">${seriesNames.map((name, i) =>
				`<span class="cpChartKey" data-series="${i}">${esc(name)}</span>`).join("")}</span>`
			: "";

		const rows = chart.points.map((point) => {
			const bars = point.values.map((value, i) => {
				const width = Math.max(value ? 1.5 : 0, Math.round((Math.abs(value) / max) * 1000) / 10);
				return `<span class="cpChartTrack"><span class="cpChartBar" data-series="${i}" style="width:${width}%"></span>` +
					`<span class="cpChartValue">${esc(texts.value(value, chart.value_type, point.unit))}</span></span>`;
			}).join("");
			const name = `<span class="cpChartLabel"><span class="cpChartId">${esc(point.label)}</span>` +
				(point.detail ? `<span class="cpChartDetail">${esc(point.detail)}</span>` : "") + "</span>";
			const description = [point.label, point.detail, ...point.values.map((value, i) =>
				`${grouped ? seriesNames[i] + " " : ""}${texts.value(value, chart.value_type, point.unit)}`)].filter(Boolean).join(", ");
			const action = actionable && point.key
				? ` role="button" tabindex="0" data-key="${esc(point.key)}" title="${esc(actionText)}"`
				: "";
			return `<div class="cpChartRow" aria-label="${esc(description)}"${action}>${name}<span class="cpChartBars">${bars}</span></div>`;
		}).join("");

		const caption = grouped ? "" : esc(seriesNames[0]);
		const shown = chart.total > chart.shown
			? `<div class="cpChartFoot">${esc(texts.text("chartShown", [chart.shown, chart.total]))}</div>`
			: "";
		return `<figure class="cpChart" data-type="${grouped ? "grouped" : "bars"}" aria-label="${esc(texts.text("chartLabel", [grouped ? seriesNames.join(", ") : seriesNames[0]]))}">` +
			`<figcaption class="cpChartCaption">${caption}${legend}</figcaption>` +
			`<div class="cpChartRows">${rows}</div>${shown}</figure>`;
	}

	return { render };
});
