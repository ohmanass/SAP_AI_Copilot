sap.ui.define([
	"sap/ui/test/Opa5",
	"sap/ui/test/matchers/PropertyStrictEquals",
	"sap/ui/test/matchers/AggregationLengthEquals"
], (Opa5, PropertyStrictEquals, AggregationLengthEquals) => {
	"use strict";

	const withClass = (styleClass) => (control) => control.hasStyleClass(styleClass);

	Opa5.createPageObjects({
		onTheSettings: {
			assertions: {
				iSeeTheLanguageChoice() {
					return this.waitFor({
						id: "languageSelect", viewName: "Settings",
						check: (select) => select.getItems().length > 4,
						success: (select) => {
							const keys = select.getItems().map((item) => item.getKey && item.getKey()).filter(Boolean);
							Opa5.assert.ok(["auto", "en", "fr", "de", "es", "it", "ja"].every((key) => keys.includes(key)),
								"automatic, reviewed and machine-translated languages");
							Opa5.assert.strictEqual(select.getSelectedKey(), "auto", "browser language by default");
						},
						errorMessage: "No language choice"
					});
				},

				iSeeMyQuota(text) {
					return this.waitFor({
						controlType: "sap.m.ProgressIndicator", viewName: "Settings",
						check: (bars) => bars.some((bar) => Math.abs(bar.getPercentValue() - 2.72) < 0.01),
						success: () => this.waitFor({
							controlType: "sap.m.Text", viewName: "Settings",
							check: (texts) => texts.some((t) => t.getText() === text),
							success: () => Opa5.assert.ok(true, `quota: ${text}`),
							errorMessage: `Quota text "${text}" not shown`
						}),
						errorMessage: "No quota gauge"
					});
				},

				iSeeTheAiUsage(calls) {
					return this.waitFor({
						controlType: "sap.m.Text", viewName: "Settings", matchers: withClass("cpKpiValue"),
						check: (texts) => texts.some((text) => text.getText() === calls),
						success: () => Opa5.assert.ok(true, `AI usage: ${calls} model calls`),
						errorMessage: "No AI usage"
					});
				}
			}
		},

		onTheKnowledgeBase: {
			assertions: {
				iSeeTheDocuments(count) {
					return this.waitFor({
						id: "documentsTable", viewName: "KnowledgeBase",
						matchers: new AggregationLengthEquals({ name: "items", length: count }),
						success: () => Opa5.assert.ok(true, `${count} document(s) listed`),
						errorMessage: "Documents not listed"
					});
				},

				iSeeTheNavigation(section) {
					return this.waitFor({
						controlType: "sap.tnt.NavigationList",
						matchers: new PropertyStrictEquals({ name: "selectedKey", value: section }),
						success: () => Opa5.assert.ok(true, `sidebar on ${section}`),
						errorMessage: `Sidebar not on ${section}`
					});
				}
			}
		}
	});
});
