/* global QUnit */
sap.ui.define(() => {
	"use strict";

	/**
	 * Publishes the QUnit result on window.__testResult for the runner
	 * (tests/test_frontend.py), with the message of every failed assertion.
	 * Required first by each test module of the suite.
	 */
	const failures = [];

	QUnit.log((details) => {
		if (!details.result) {
			failures.push({
				test: `${details.module} > ${details.name}`,
				message: details.message || "",
				expected: details.expected === undefined ? undefined : String(details.expected),
				actual: details.actual === undefined ? undefined : String(details.actual)
			});
		}
	});

	QUnit.done((details) => {
		window.__testResult = {
			total: details.total,
			passed: details.passed,
			failed: details.failed,
			runtime: details.runtime,
			failures
		};
	});
});
