sap.ui.define([
	"./Language"
], (Language) => {
	"use strict";

	/**
	 * Excel export of a SAP data table (sap.ui.export.Spreadsheet, built in the browser).
	 *
	 * Every column of the table is exported, including the ones hidden on narrow
	 * screens, with typed cells: numbers, amounts with their currency, quantities
	 * with their unit, real dates. A second sheet describes where the data comes
	 * from (SAP system, time window, totals, document header).
	 */

	// Characters not allowed (or badly handled) in file names on Windows and macOS
	const FORBIDDEN_FILE_CHARS = /[\\/:*?"<>|·]+/g;

	const isEmpty = (value) => value === null || value === undefined || value === "";

	/** "2026-11-17" -> Date at UTC midnight (the Spreadsheet writes dates in UTC). */
	function toDate(value) {
		if (isEmpty(value)) {
			return null;
		}
		const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value));
		return match ? new Date(Date.UTC(+match[1], +match[2] - 1, +match[3])) : null;
	}

	function toNumber(value) {
		return isEmpty(value) || isNaN(Number(value)) ? null : Number(value);
	}

	function today() {
		return new Date().toISOString().slice(0, 10);
	}

	return {

		/**
		 * Builds the spreadsheet settings (no UI5 dependency: easy to check).
		 * @param {object} table Table description from the backend
		 * @param {object} texts Helpers: title, label(key), statusText(value), blockText(value),
		 *        attributeText(attribute), text(key, args)
		 * @param {object} EdmType sap.ui.export.EdmType
		 */
		settings(table, texts, EdmType) {
			const columns = table.columns.map((column) => {
				const setting = { label: texts.label(column.key), property: column.key, width: 14 };
				switch (column.type) {
					case "amount":
						Object.assign(setting, { type: EdmType.Currency, displayUnit: true, scale: 2, width: 16 });
						if (column.unit_key) {
							setting.unitProperty = column.unit_key;
						}
						break;
					case "quantity":
					case "number":
						// The unit goes in its own column (below): Excel keeps a pure number
						Object.assign(setting, { type: EdmType.Number, delimiter: true });
						break;
					case "days":
						Object.assign(setting, { type: EdmType.Number });
						break;
					case "date":
						Object.assign(setting, { type: EdmType.Date, width: 12 });
						break;
					case "text":
						Object.assign(setting, { type: EdmType.String, width: 28, wrap: true });
						break;
					default:
						setting.type = EdmType.String;
				}
				return setting;
			});

			// Quantity units get their own column, right after the quantity
			const withUnits = [];
			columns.forEach((setting, index) => {
				withUnits.push(setting);
				const column = table.columns[index];
				if (["quantity", "number"].includes(column.type) && column.unit_key) {
					withUnits.push({
						label: texts.text("dataExportUnit", [setting.label]),
						property: `${column.key}__unit`,
						type: EdmType.String,
						width: 7
					});
				}
			});

			const rows = table.rows.map((row) => {
				const out = {};
				table.columns.forEach((column) => {
					const value = row[column.key];
					switch (column.type) {
						case "amount":
						case "quantity":
						case "number":
						case "days":
							out[column.key] = toNumber(value);
							break;
						case "date":
							out[column.key] = toDate(value);
							break;
						case "status":
							out[column.key] = texts.statusText(value);
							break;
						case "block":
							out[column.key] = isEmpty(value) ? "" : texts.blockText(value);
							break;
						default:
							out[column.key] = isEmpty(value) ? "" : String(value);
					}
					if (column.unit_key) {
						const unit = isEmpty(row[column.unit_key]) ? "" : String(row[column.unit_key]);
						if (column.type === "amount") {
							out[column.unit_key] = unit;
						} else {
							out[`${column.key}__unit`] = unit;
						}
					}
				});
				return out;
			});

			// Information sheet: provenance and document header
			const items = [];
			if (table.source) {
				items.push({ key: texts.text("dataExportSource"), value: table.source });
			}
			if (table.window) {
				items.push({ key: texts.text("dataExportWindow"), value: table.window });
			}
			items.push({ key: texts.text("dataExportRows"), value: String(table.rows.length) });
			if (!isEmpty(table.total)) {
				items.push({ key: texts.text("dataExportTotal"), value: String(table.total) });
			}
			items.push({ key: texts.text("dataExportDate"), value: new Date().toLocaleString(Language.locale()) });

			const header = (table.attributes || []).map((attribute) => ({
				key: texts.label(attribute.key),
				value: texts.attributeText(attribute)
			}));

			const metainfo = [{ name: texts.text("dataExportProvenance"), items }];
			if (header.length) {
				metainfo.push({ name: texts.text("dataExportHeader"), items: header });
			}

			const fileTitle = texts.title.replace(FORBIDDEN_FILE_CHARS, " - ").replace(/\s+/g, " ")
				.replace(/(\s-)+\s/g, " - ").trim();

			return {
				workbook: {
					columns: withUnits,
					context: {
						application: "SAP AI Copilot",
						title: texts.title,
						sheetName: texts.text("dataExportSheet"),
						metaSheetName: texts.text("dataExportInfoSheet"),
						metainfo
					}
				},
				dataSource: rows,
				fileName: `${fileTitle} ${today()}.xlsx`,
				// Small data: no web worker (it would be loaded cross-origin from the SAPUI5 CDN on SAP BTP)
				worker: false
			};
		},

		/** Builds and downloads the file. Resolves when the download has started. */
		download(table, texts) {
			return new Promise((resolve, reject) => {
				sap.ui.require(["sap/ui/export/Spreadsheet", "sap/ui/export/library"], (Spreadsheet, exportLibrary) => {
					const spreadsheet = new Spreadsheet(this.settings(table, texts, exportLibrary.EdmType));
					spreadsheet.build()
						.then(resolve, reject)
						.finally(() => spreadsheet.destroy());
				}, reject);
			});
		}
	};
});
