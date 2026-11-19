sap.ui.define(() => {
	"use strict";

	/**
	 * Test suite of SAP AI Copilot (SAPUI5 Test Starter).
	 *
	 * Every test runs in Test.qunit.html?testsuite=…&test=<name>; all of them:
	 * testsuite.qunit.html. In the CI they run from pytest (tests/test_frontend.py).
	 */
	return {
		name: "SAP AI Copilot",
		defaults: {
			page: "ui5://test-resources/com/sapaicopilot/app/Test.qunit.html?testsuite={suite}&test={name}",
			qunit: { version: 2, reorder: false, testTimeout: 60000 },
			sinon: false,
			ui5: { theme: "sap_horizon_dark", language: "en" },
			loader: {
				paths: { "com/sapaicopilot/app": "../" }
			}
		},
		tests: {
			"unit/unitTests": {
				title: "Unit tests: rendering, charts, export, language, dictation, formatters"
			},
			"integration/opaTests": {
				title: "OPA5 journeys on a simulated backend",
				autostart: true
			}
		}
	};
});
