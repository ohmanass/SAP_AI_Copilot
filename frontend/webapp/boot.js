sap.ui.define([
	"./util/Language"
], (Language) => {
	"use strict";

	/**
	 * Entry point (index.html, data-sap-ui-on-init): the interface language is
	 * set before the component exists, so every text, date and number is created
	 * in that language. Then ComponentSupport creates the app from index.html.
	 */
	return Language.apply().finally(() => {
		sap.ui.require(["sap/ui/core/ComponentSupport"]);
	});
});
