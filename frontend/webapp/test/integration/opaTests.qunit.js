sap.ui.define([
	"sap/ui/test/Opa5",
	"../TestResult",
	"./arrangements/Startup",
	"./ChatJourney",
	"./NavigationJourney"
], (Opa5, TestResult, Startup) => {
	"use strict";

	// OPA5 journeys of the real app on a simulated backend (run by the Test Starter, see ../testsuite.qunit.js)
	Opa5.extendConfig({
		arrangements: new Startup(),
		viewNamespace: "com.sapaicopilot.app.view.",
		autoWait: true,
		timeout: 30
	});
});
