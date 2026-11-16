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
	"sap/ui/core/ResizeHandler",
	"sap/ui/core/format/DateFormat",
	"sap/ui/core/format/NumberFormat"
], (VBox, HBox, FlexBox, Title, Text, Label, Table, Column, ColumnListItem, ObjectNumber, ObjectStatus,
	ResizeHandler, DateFormat, NumberFormat) => {
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
		NOT_RELEVANT: "None"
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

	return {

		/**
		 * Factory for the "tables" aggregation of a message.
		 * @param {sap.ui.core.mvc.Controller} controller Chat controller (i18n, row press)
		 */
		create(controller, id, context) {
			const table = context.getObject();
			const text = (key, args) => controller.getText(key, args);
			const optionalText = (key, fallback) => {
				const bundle = controller.getOwnerComponent().getModel("i18n").getResourceBundle();
				return bundle.hasText(key) ? bundle.getText(key) : fallback;
			};

			const label = (key) => optionalText(`dataCol.${key}`, key);
			const statusText = (value) => (isEmpty(value) ? "" : optionalText(`dataStatus.${value}`, String(value)));

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
					case "id":
						return new Text({ text: { path }, wrapping: false }).addStyleClass("cpDataId");
					default:
						return new Text({ text: { path } });
				}
			};

			const container = new VBox(id, { renderType: "Bare" }).addStyleClass("cpDataTable");

			// ---------- Header: title and provenance ----------
			const title = [optionalText(`dataTable.${table.kind}`, table.kind)].concat(table.title_params || []).join(" · ");
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

			container.addItem(new HBox({
				renderType: "Bare",
				alignItems: "Center",
				items: [
					new Title({ text: title, level: "H3", wrapping: true }).addStyleClass("cpDataTitle"),
					new Text({ text: table.rows.length ? text("dataRows", [table.rows.length]) : "" }).addStyleClass("cpDataCount")
				]
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
							Object.assign(settings, { minScreenWidth: "32rem", demandPopin: true, importance: "Medium" });
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
					const width = domRef ? Math.floor(domRef.clientWidth) : 0;
					if (width && sapTable.getContextualWidth() !== `${width}px`) {
						sapTable.setContextualWidth(`${width}px`);
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
