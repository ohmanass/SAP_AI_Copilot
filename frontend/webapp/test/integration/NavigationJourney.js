/* global QUnit */
sap.ui.define([
	"sap/ui/test/opaQunit",
	"./pages/Pages"
], (opaTest) => {
	"use strict";

	QUnit.module("Navigation journey");

	opaTest("Settings: language choice and AI usage", (Given, When, Then) => {
		Given.iStartMyApp("settings");
		Then.onTheKnowledgeBase.iSeeTheNavigation("settings");
		Then.onTheSettings.iSeeTheLanguageChoice()
			.and.iSeeTheAiUsage("5");
		Then.iTeardownMyApp();
	});

	opaTest("Knowledge Base: the indexed documents", (Given, When, Then) => {
		Given.iStartMyApp("knowledge-base");
		Then.onTheKnowledgeBase.iSeeTheNavigation("knowledgeBase")
			.and.iSeeTheDocuments(1);
		Then.iTeardownMyApp();
	});
});
