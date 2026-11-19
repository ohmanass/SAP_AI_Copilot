sap.ui.define([
	"sap/ui/test/Opa5",
	"sap/ui/test/actions/Press",
	"sap/ui/test/actions/EnterText",
	"sap/ui/test/matchers/PropertyStrictEquals",
	"../mock/MockBackend"
], (Opa5, Press, EnterText, PropertyStrictEquals, MockBackend) => {
	"use strict";

	const VIEW = "Chat";
	const withClass = (styleClass) => (control) => control.hasStyleClass(styleClass);

	Opa5.createPageObjects({
		onTheChat: {
			actions: {
				iAsk(question) {
					this.waitFor({
						id: "input", viewName: VIEW,
						actions: new EnterText({ text: question, keepFocus: true }),
						errorMessage: "No message field"
					});
					return this.waitFor({
						id: "sendButton", viewName: VIEW, matchers: new PropertyStrictEquals({ name: "enabled", value: true }),
						actions: new Press(), errorMessage: "The send button stays disabled"
					});
				},

				iAnalyseTheFirstIndicator() {
					return this.waitFor({
						controlType: "sap.m.Button", viewName: VIEW,
						matchers: [withClass("cpDashAction"), new PropertyStrictEquals({ name: "enabled", value: true })],
						success: (buttons) => new Press().executeOn(buttons[0]),
						errorMessage: "No Analyse button on the dashboard"
					});
				},

				iPressTheFollowUp(text) {
					return this.waitFor({
						controlType: "sap.m.Button", viewName: VIEW,
						matchers: [withClass("cpFollowup"), new PropertyStrictEquals({ name: "text", value: text })],
						actions: new Press(), errorMessage: `No follow-up "${text}"`
					});
				}
			},

			assertions: {
				iSeeTheDashboard(values) {
					return this.waitFor({
						controlType: "sap.m.Text", viewName: VIEW, matchers: withClass("cpDashValue"),
						check: (texts) => texts.length === values.length,
						success: (texts) => Opa5.assert.deepEqual(texts.map((text) => text.getText()), values,
							"live SAP indicators (an unavailable one shows a dash)"),
						errorMessage: "No dashboard"
					});
				},

				iSeeMyQuestion(question) {
					return this.waitFor({
						controlType: "sap.m.Text", viewName: VIEW,
						matchers: [withClass("cpUserBubble"), new PropertyStrictEquals({ name: "text", value: question })],
						success: () => Opa5.assert.ok(true, `question shown: ${question}`),
						errorMessage: `Question not shown: ${question}`
					});
				},

				iSeeTheAnswerWithItsChart() {
					return this.waitFor({
						controlType: "sap.ui.core.HTML", viewName: VIEW,
						check: (controls) => controls.some((control) => (control.getContent() || "").includes("cpChartRow")),
						success: (controls) => {
							const chart = controls.find((control) => control.getContent().includes("cpChartRow")).getDomRef();
							const rows = [...chart.querySelectorAll(".cpChartRow")];
							Opa5.assert.deepEqual(rows.map((row) => row.getAttribute("data-key")), ["4500000010", "4500000011"],
								"chart of the SAP rows, largest first, each bar opens its order");
						},
						errorMessage: "No chart under the answer"
					});
				},

				iSeeTheAnswerText(part) {
					return this.waitFor({
						controlType: "sap.ui.core.HTML", viewName: VIEW,
						check: (controls) => controls.some((control) => (control.getDomRef() || {}).textContent?.includes(part)),
						success: () => Opa5.assert.ok(true, `answer shown: ${part}`),
						errorMessage: `Answer not shown: ${part}`
					});
				},

				iSeeTheTitle(title) {
					return this.waitFor({
						controlType: "sap.m.Title", viewName: VIEW,
						matchers: [withClass("cpTopbarTitle"), new PropertyStrictEquals({ name: "text", value: title })],
						success: () => Opa5.assert.ok(true, `title written by the assistant: ${title}`),
						errorMessage: `Title not "${title}"`
					});
				},

				iSeeTheFollowUps(texts) {
					return this.waitFor({
						controlType: "sap.m.Button", viewName: VIEW, matchers: withClass("cpFollowup"),
						check: (buttons) => buttons.filter((button) => button.getDomRef()?.offsetParent).length === texts.length,
						success: (buttons) => Opa5.assert.deepEqual(
							buttons.filter((button) => button.getDomRef()?.offsetParent).map((button) => button.getText()), texts,
							"follow-up questions under the latest answer"),
						errorMessage: "No follow-up questions"
					});
				},

				theAssistantWasAsked(questions) {
					return this.waitFor({
						check: () => MockBackend.questions().length === questions.length,
						success: () => Opa5.assert.deepEqual(MockBackend.questions(), questions, "questions sent to the backend"),
						errorMessage: "Questions not sent"
					});
				}
			}
		}
	});
});
