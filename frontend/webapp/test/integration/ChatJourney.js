/* global QUnit */
sap.ui.define([
	"sap/ui/test/opaQunit",
	"./pages/Chat"
], (opaTest) => {
	"use strict";

	QUnit.module("Chat journey");

	opaTest("The home screen shows the live SAP indicators", (Given, When, Then) => {
		Given.iStartMyApp();
		Then.onTheChat.iSeeTheDashboard(["3,315", "2,810", "—"]);
	});

	opaTest("An indicator asks the assistant; the answer comes with its table, chart, title and follow-ups", (Given, When, Then) => {
		When.onTheChat.iAnalyseTheFirstIndicator();

		Then.onTheChat.iSeeMyQuestion("Show overdue purchase orders.")
			.and.iSeeTheAnswerText("overdue purchase orders, the oldest one at EV Parts")
			.and.iSeeTheAnswerWithItsChart()
			.and.iSeeTheTitle("Overdue purchase orders of EV Parts")
			.and.iSeeTheFollowUps(["Analyse purchase order 4500000010", "Check the invoices of EV Parts"]);
	});

	opaTest("A follow-up question is sent with one click, then a typed one", (Given, When, Then) => {
		When.onTheChat.iPressTheFollowUp("Check the invoices of EV Parts");
		Then.onTheChat.iSeeMyQuestion("Check the invoices of EV Parts");

		When.onTheChat.iAsk("And the oldest order?");
		Then.onTheChat.iSeeMyQuestion("And the oldest order?")
			.and.theAssistantWasAsked(["Show overdue purchase orders.", "Check the invoices of EV Parts", "And the oldest order?"]);

		Then.iTeardownMyApp();
	});
});
