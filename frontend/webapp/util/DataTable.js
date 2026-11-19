sap.ui.define([
	"sap/m/VBox",
	"sap/m/HBox",
	"sap/m/FlexBox",
	"sap/m/Title",
	"sap/m/Text",
	"sap/m/Label",
	"sap/m/Table",
	"sap/m/Column",
	"sap/m/ColumnListItem",
	"sap/m/ObjectNumber",
	"sap/m/ObjectStatus",
	"sap/m/Button",
	"sap/m/MessageToast",
	"sap/base/Log",
	"sap/ui/core/ResizeHandler",
	"sap/ui/core/format/DateFormat",
	"sap/ui/core/format/NumberFormat",
	"sap/ui/core/HTML",
	"./DataTableExport",
	"./DataChart"
], (VBox, HBox, FlexBox, Title, Text, Label, Table, Column, ColumnListItem, ObjectNumber, ObjectStatus,
	Button, MessageToast, Log, ResizeHandler, DateFormat, NumberFormat, HTML, DataTableExport, DataChart) => {
	"use strict";

	/**
	 * SAP data table shown under an assistant answer.
	 *
	 * The table description comes from the backend (app/agent/data_tables.py):
	 * every value is read from the SAP tool results, never from the model's text.
	 * Rendering uses UI5 controls only (no HTML), so values are always displayed as text.
	 */

	const isoDate = DateFormat.getDateInstance({ pattern: "yyyy-MM-dd", UTC: true });
	const displayDate = DateFormat.getDateInstance({ style: "medium", UTC: true });
	const amountFormat = NumberFormat.getFloatInstance({ minFractionDigits: 2, maxFractionDigits: 2, groupingEnabled: true });
	const numberFormat = NumberFormat.getFloatInstance({ maxFractionDigits: 3, groupingEnabled: true });
	const integerFormat = NumberFormat.getIntegerInstance({ groupingEnabled: true });

	// Below this table width, columns from the third one move into the row popin
	const POPIN_WIDTH = "32rem";
	const POPIN_WIDTH_PX = 32 * 16;

	// Semantic colours of SAP statuses (purchase orders and sales orders)
	const STATUS_STATES = {
		OVERDUE: "Error",
		OPEN: "Information",
		PARTIALLY_RECEIVED: "Warning",
		RECEIVED: "Success",
		NO_ACTIVE_ITEMS: "None",
		NOT_DELIVERED: "Warning",
		PARTIALLY_DELIVERED: "Warning",
		DELIVERED: "Success",
		IN_PROCESS: "Warning",
		COMPLETED: "Success",
		NOT_RELEVANT: "None",
		// Supplier invoices and three-way match
		POSTED: "Success",
		PARKED: "Warning",
		HELD: "Warning",
		MATCHED: "Success",
		FULLY_MATCHED: "Success",
		PARTIALLY_INVOICED: "Warning",
		NOT_INVOICED: "Warning",
		NOT_RECEIVED: "None",
		IN_PROGRESS: "Information",
		VARIANCE: "Error",
		QUANTITY_VARIANCE: "Error",
		PRICE_VARIANCE: "Error",
		INVOICED_NOT_RECEIVED: "Error"
	};

	const isEmpty = (value) => value === null || value === undefined || value === "";

	function formatDate(value) {
		if (isEmpty(value)) {
			return "";
		}
		const date = isoDate.parse(String(value).slice(0, 10));
		return date ? displayDate.format(date) : String(value);
	}

	function formatNumber(value, type) {
		if (isEmpty(value) || isNaN(Number(value))) {
			return isEmpty(value) ? "" : String(value);
		}
		if (type === "amount") {
			return amountFormat.format(Number(value));
		}
		return Number.isInteger(Number(value)) ? integerFormat.format(Number(value)) : numberFormat.format(Number(value));
	}

	/** Translated texts of a table: labels, statuses, title and value formatting. */
	function helpers(controller) {
		const text = (key, args) => controller.getText(key, args);
		const optionalText = (key, fallback) => {
			const bundle = controller.getOwnerComponent().getModel("i18n").getResourceBundle();
			return bundle.hasText(key) ? bundle.getText(key) : fallback;
		};
		const label = (key) => optionalText(`dataCol.${key}`, key);
		const statusText = (value) => (isEmpty(value) ? "" : optionalText(`dataStatus.${value}`, String(value)));
		const blockText = (value) => (isEmpty(value) ? "" : text("dataBlocked", [value]));
		const title = (table) => [optionalText(`dataTable.${table.kind}`, table.kind)].concat(table.title_params || []).join(" · ");

		/** Display text of a value of a given column type (unit appended when given). */
		const valueText = (value, type, unit) => {
			switch (type) {
				case "date":
					return formatDate(value);
				case "amount":
				case "quantity":
				case "number":
					return isEmpty(value) ? "" : [formatNumber(value, type), unit].filter(Boolean).join(" ");
				case "status":
					return statusText(value);
				case "block":
					return blockText(value);
				case "days":
					return isEmpty(value) ? "" : text("dataDays", [formatNumber(value, "number")]);
				default:
					return isEmpty(value) ? "" : String(value);
			}
		};
		return { text, optionalText, label, statusText, blockText, title, valueText };
	}

	/** The chart as an HTML control; a bar of an actionable table asks for its document. */
	function chartControl(controller, table, texts) {
		const chart = table.chart;
		const actionText = chart.action ? texts.text(`dataRowAction.${chart.action}`) : "";
		const html = new HTML({ content: DataChart.render(chart, texts, actionText), sanitizeContent: false });
		if (chart.action) {
			const ask = (event) => {
				const bar = event.target.closest && event.target.closest(".cpChartRow[data-key]");
				if (bar) {
					controller.askForDocument(chart.action, bar.getAttribute("data-key"));
				}
			};
			html.attachAfterRendering(() => {
				const dom = html.getDomRef();
				if (dom && !dom._cpChartEvents) {
					dom._cpChartEvents = true;
					dom.addEventListener("click", ask);
					dom.addEventListener("keydown", (event) => {
						if (event.key === "Enter" || event.key === " ") {
							event.preventDefault();
							ask(event);
						}
					});
				}
			});
		}
		return html;
	}

	return {

		/**
		 * A table as plain texts (conversation export): title, source, header
		 * attributes, column labels and formatted rows.
		 */
		toText(controller, table) {
			const h = helpers(controller);
			return {
				title: h.title(table),
				meta: [table.source, table.window].filter(Boolean),
				attributes: (table.attributes || []).map((attribute) => ({
					label: h.label(attribute.key),
					value: h.valueText(attribute.value, attribute.type, attribute.unit)
				})),
				columns: table.columns.map((column) => h.label(column.key)),
				rows: table.rows.map((row) => table.columns.map((column) =>
					h.valueText(row[column.key], column.type, column.unit_key ? row[column.unit_key] : ""))),
				total: table.total && table.total > table.rows.length
					? h.text("dataShown", [integerFormat.format(table.rows.length), integerFormat.format(table.total)])
					: ""
			};
		},

		/**
		 * Factory for the "tables" aggregation of a message.
		 * @param {sap.ui.core.mvc.Controller} controller Chat controller (i18n, row press)
		 */
		create(controller, id, context) {
			const table = context.getObject();
			const { text, optionalText, label, statusText, blockText, valueText } = helpers(controller);

			/** Value control for a column type, bound to a row (or attribute) property. */
			const valueControl = (type, path, unitPath) => {
				switch (type) {
					case "date":
						return new Text({ text: { path, formatter: formatDate }, wrapping: false });
					case "number":
					case "quantity":
					case "amount":
						return new ObjectNumber({
							number: { path, formatter: (value) => formatNumber(value, type) },
							unit: unitPath ? { path: unitPath } : "",
							emphasized: type === "amount",
							textAlign: path === "chat>value" ? "Begin" : "End"
						});
					case "status":
						return new ObjectStatus({
							text: { path, formatter: statusText },
							state: { path, formatter: (value) => STATUS_STATES[value] || "None" }
						});
					case "days":
						return new ObjectStatus({
							text: { path, formatter: (value) => (isEmpty(value) ? "" : text("dataDays", [integerFormat.format(value)])) },
							state: { path, formatter: (value) => (value > 14 ? "Error" : value > 0 ? "Warning" : "None") },
							inverted: false
						});
					case "block":
						// Payment block code (e.g. A, R): shown in red when set
						return new ObjectStatus({
							text: { path, formatter: blockText },
							state: "Error",
							icon: { path, formatter: (value) => (isEmpty(value) ? "" : "sap-icon://locked") }
						});
					case "id":
						return new Text({ text: { path }, wrapping: false }).addStyleClass("cpDataId");
					default:
						return new Text({ text: { path } });
				}
			};

			const container = new VBox(id, { renderType: "Bare" }).addStyleClass("cpDataTable");

			// ---------- Header: title and provenance ----------
			const title = helpers(controller).title(table);
			const meta = [];
			if (table.source) {
				meta.push(table.source);
			}
			if (table.window) {
				meta.push(table.window);
			}
			if (table.rows.length && table.total !== null && table.total !== undefined && table.total > table.rows.length) {
				meta.push(text("dataShown", [integerFormat.format(table.rows.length), integerFormat.format(table.total)]) +
					" " + optionalText(`dataTotalUnit.${table.total_unit}`, ""));
			}

			const headerItems = [
				new Title({ text: title, level: "H3", wrapping: true }).addStyleClass("cpDataTitle"),
				new Text({ text: table.rows.length ? text(table.rows.length === 1 ? "dataRow" : "dataRows", [table.rows.length]) : "" }).addStyleClass("cpDataCount")
			];
			if (table.rows.length) {
				headerItems.push(new Button({
					icon: "sap-icon://excel-attachment",
					type: "Transparent",
					tooltip: text("dataExport"),
					press: (event) => {
						const button = event.getSource();
						button.setEnabled(false);
						DataTableExport.download(table, {
							title,
							label,
							statusText,
							blockText,
							text,
							attributeText: (attribute) => valueText(attribute.value, attribute.type, attribute.unit)
						}).catch((error) => {
							// e.g. sap.ui.export not served: the UI5 dev server reads ui5.yaml at startup only
							Log.error("Excel export failed", error && (error.message || String(error)), "com.sapaicopilot.app.DataTable");
							MessageToast.show(text("dataExportError"));
						})
							.finally(() => button.setEnabled(true));
					}
				}).addStyleClass("cpDataExport"));
			}

			container.addItem(new HBox({
				renderType: "Bare",
				alignItems: "Center",
				items: headerItems
			}).addStyleClass("cpDataHeader"));

			if (meta.length) {
				container.addItem(new Text({ text: meta.join("  ·  ") }).addStyleClass("cpDataMeta"));
			}

			// ---------- Document header attributes (object page style) ----------
			if (table.attributes.length) {
				container.addItem(new FlexBox({
					renderType: "Bare",
					wrap: "Wrap",
					items: {
						path: "chat>attributes",
						templateShareable: false,
						factory: (attributeId, attributeContext) => {
							const attribute = attributeContext.getObject();
							return new VBox(attributeId, {
								renderType: "Bare",
								items: [
									new Label({ text: label(attribute.key) }),
									valueControl(attribute.type, "chat>value", attribute.unit ? "chat>unit" : null)
								]
							}).addStyleClass("cpDataAttribute");
						}
					}
				}).addStyleClass("cpDataAttributes"));
			}

			// ---------- Chart of the rows (built by the backend, app/agent/charts.py) ----------
			if (table.chart) {
				container.addItem(chartControl(controller, table, { text, label, value: valueText }));
			}

			// ---------- Rows ----------
			if (table.rows.length) {
				const actionable = !!(table.row_action && table.row_key);
				const columns = table.columns;
				const rowSettings = {
					type: actionable ? "Navigation" : "Inactive",
					cells: columns.map((column) => valueControl(
						column.type,
						`chat>${column.key}`,
						column.unit_key ? `chat>${column.unit_key}` : null
					))
				};
				if (actionable) {
					rowSettings.press = (event) => controller.onDataRowPress(event, table.row_action, table.row_key);
				}

				const sapTable = new Table({
					fixedLayout: false,
					popinLayout: "GridSmall",
					// Secondary columns are hidden rather than added under each row
					hiddenInPopin: ["Low"],
					growing: true,
					growingThreshold: 10,
					growingTriggerText: text("dataMore"),
					columns: columns.map((column, index) => {
						const settings = {
							header: new Text({ text: label(column.key), wrapping: false }),
							hAlign: ["number", "quantity", "amount"].includes(column.type) ? "End" : "Begin"
						};
						// Widths compare to the table itself (contextualWidth). The first two columns
						// always stay visible; "low" columns are hidden below 60rem (always the case
						// in the chat column); the others move into the row popin on phones.
						if (index > 0 && column.importance === "low") {
							Object.assign(settings, { minScreenWidth: "60rem", demandPopin: true, importance: "Low" });
						} else if (index > 1) {
							Object.assign(settings, { minScreenWidth: POPIN_WIDTH, demandPopin: true, importance: "Medium" });
						}
						return new Column(settings);
					}),
					items: {
						path: "chat>rows",
						templateShareable: false,
						template: new ColumnListItem(rowSettings)
					}
				}).addStyleClass("cpDataGrid");

				if (actionable) {
					sapTable.setTooltip(text(`dataRowAction.${table.row_action}`));
				}
				container.addItem(sapTable);

				// Popin decisions follow the card width (the chat column), not the screen width.
				// Measured on the card: with an automatic layout the table itself grows with its content.
				// The registration is removed automatically when the container is destroyed.
				const applyWidth = () => {
					const domRef = container.getDomRef();
					if (!domRef) {
						return;
					}
					// Content width of the card (clientWidth includes its padding)
					const style = getComputedStyle(domRef);
					const width = Math.floor(domRef.clientWidth
						- parseFloat(style.paddingLeft) - parseFloat(style.paddingRight));
					if (width <= 0) {
						return;
					}
					if (sapTable.getContextualWidth() !== `${width}px`) {
						sapTable.setContextualWidth(`${width}px`);
					}
					// With popins (narrow card), an automatic layout lets the popin row widen the
					// table past the card: a fixed layout keeps it exactly at the card width.
					const narrow = width < POPIN_WIDTH_PX;
					if (sapTable.getFixedLayout() !== narrow) {
						sapTable.setFixedLayout(narrow);
					}
				};
				container.addEventDelegate({
					onAfterRendering: () => {
						applyWidth();
						if (!container._cpResizeId) {
							container._cpResizeId = ResizeHandler.register(container, applyWidth);
						}
					}
				});
			}

			return container;
		}
	};
});
