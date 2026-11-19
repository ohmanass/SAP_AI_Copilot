sap.ui.define([
	"sap/ui/test/Opa5",
	"../mock/MockBackend"
], (Opa5, MockBackend) => {
	"use strict";

	/** Starts the real component on the simulated backend. */
	return Opa5.extend("com.sapaicopilot.app.test.integration.arrangements.Startup", {

		/** Each journey starts on a fresh simulated backend; Then.iTeardownMyApp() stops the component. */
		iStartMyApp(hash = "") {
			MockBackend.install();
			return this.iStartMyUIComponent({
				componentConfig: { name: "com.sapaicopilot.app", async: true, manifest: true },
				hash
			});
		}
	});
});
