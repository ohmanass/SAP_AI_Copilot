sap.ui.define([
	"sap/m/Dialog",
	"sap/m/Button",
	"sap/m/Label",
	"sap/m/Select",
	"sap/m/Text",
	"sap/m/VBox",
	"sap/m/HBox",
	"sap/ui/core/Item"
], (Dialog, Button, Label, Select, Text, VBox, HBox, Item) => {
	"use strict";

	/**
	 * "Add to the knowledge base" dialog: workspace of the document and, when no
	 * document is given (Knowledge Base page), the file to upload.
	 * Resolves with { module, file } or null when cancelled.
	 */

	const EXTENSIONS = [
		".pdf", ".docx", ".xlsx", ".txt", ".md", ".csv", ".tsv", ".json", ".xml", ".log",
		".sql", ".abap", ".yaml", ".yml", ".html", ".htm",
		// Images: read by the AI model (app/attachments/vision.py)
		".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"
	];
	const MAX_BYTES = 10 * 1024 * 1024;

	return {

		/**
		 * @param {sap.ui.core.mvc.Controller} controller Owner (i18n, modules, dependent)
		 * @param {object} options { documentName?: string, module?: string }
		 */
		open(controller, options = {}) {
			const text = (key, args) => controller.getText(key, args);
			const modules = (controller.getModel("app").getProperty("/modules") || [])
				.filter((m) => m.status !== "planned" && (m.tools || []).includes("search_sap_documentation"));

			return new Promise((resolve) => {
				let file = null;
				// Result given back when the dialog is closed (Escape or Cancel: null)
				let result = null;
				const finish = (value) => {
					result = value;
					dialog.close();
				};

				const select = new Select({
					width: "100%",
					selectedKey: options.module || "",
					items: [new Item({ key: "", text: text("kbModuleAll") })].concat(
						modules.map((m) => new Item({ key: m.key, text: `${m.code} · ${m.name}` }))
					)
				});

				const fileName = new Text({ text: text("kbNoFile") }).addStyleClass("cpKbFileName");
				const addButton = new Button({
					text: text("kbAddConfirm"),
					type: "Emphasized",
					enabled: !!options.documentName,
					press: () => finish({ module: select.getSelectedKey() || null, file })
				});

				const content = [
					new Text({
						text: options.documentName
							? text("kbAddAttachmentText", [options.documentName])
							: text("kbAddText")
					}).addStyleClass("cpKbDialogText"),
					new Label({ text: text("kbModuleLabel"), labelFor: select }),
					select
				];

				if (!options.documentName) {
					const chooser = new Button({
						text: text("kbChooseFile"),
						icon: "sap-icon://attachment",
						press: () => {
							const picker = document.createElement("input");
							picker.type = "file";
							picker.accept = EXTENSIONS.join(",");
							picker.addEventListener("change", () => {
								const chosen = picker.files && picker.files[0];
								if (!chosen) {
									return;
								}
								const extension = (/\.[^.]+$/.exec(chosen.name) || [""])[0].toLowerCase();
								if (!EXTENSIONS.includes(extension)) {
									fileName.setText(text("attachmentUnsupported", [chosen.name]));
									file = null;
								} else if (chosen.size > MAX_BYTES) {
									fileName.setText(text("attachmentTooLarge", [chosen.name]));
									file = null;
								} else {
									fileName.setText(chosen.name);
									file = chosen;
								}
								addButton.setEnabled(!!file);
							});
							picker.click();
						}
					});
					content.push(
						new Label({ text: text("kbFileLabel") }),
						new HBox({ alignItems: "Center", renderType: "Bare", items: [chooser, fileName] })
							.addStyleClass("cpKbFileRow")
					);
				}

				const dialog = new Dialog({
					title: text("kbAddTitle"),
					contentWidth: "28rem",
					content: [new VBox({ renderType: "Bare", items: content }).addStyleClass("cpKbDialog")],
					beginButton: addButton,
					endButton: new Button({ text: text("cancel"), press: () => finish(null) }),
					afterClose: () => {
						dialog.destroy();
						resolve(result);
					}
				}).addStyleClass("cpDialog");

				controller.getView().addDependent(dialog);
				dialog.open();
			});
		}
	};
});
