sap.ui.define([
	"sap/ui/core/format/DateFormat",
	"sap/ui/core/format/FileSizeFormat"
], (DateFormat, FileSizeFormat) => {
	"use strict";

	const relativeFormat = DateFormat.getDateTimeInstance({ relative: true, relativeScale: "auto", style: "short" });
	const timeFormat = DateFormat.getTimeInstance({ style: "short" });
	const dateTimeFormat = DateFormat.getDateTimeInstance({ style: "medium" });
	const fileSizeFormat = FileSizeFormat.getInstance({ binaryFilesize: true, maxFractionDigits: 1 });

	const toDate = (value) => (value instanceof Date ? value : value ? new Date(value) : null);

	/**
	 * Formatters are referenced from views as ".formatter.xxx", so "this" is the controller.
	 */
	return {

		upper(value) {
			return value ? String(value).toUpperCase() : "";
		},

		moduleStatusText(status) {
			return status ? this.getText(`moduleStatus.${status}`) : "";
		},

		moduleStatusState(status) {
			return { connected: "Success", demo: "Information", documentation: "Information", planned: "None" }[status] || "None";
		},

		relativeTime(value) {
			const date = toDate(value);
			return date ? relativeFormat.format(date) : "";
		},

		messageTime(value) {
			const date = toDate(value);
			return date ? timeFormat.format(date) : "";
		},

		dateTime(value) {
			const date = toDate(value);
			return date ? dateTimeFormat.format(date) : "";
		},

		fileSize(bytes) {
			return typeof bytes === "number" ? fileSizeFormat.format(bytes) : "";
		},

		kbStatusText(status) {
			return status ? this.getText(`kbStatus.${status}`) : "";
		},

		kbStatusState(status) {
			return { indexed: "Success", outdated: "Warning", not_indexed: "Error" }[status] || "None";
		},

		kbStatusIcon(status) {
			return {
				indexed: "sap-icon://status-positive",
				outdated: "sap-icon://status-critical",
				not_indexed: "sap-icon://status-negative"
			}[status] || "";
		},

		kbChunks(indexed, expected) {
			return typeof expected === "number"
				? this.getText("kbChunksExpected", [indexed, expected])
				: this.getText("kbChunksValue", [indexed]);
		},

		availability(available) {
			return this.getText(available ? "valueAvailable" : "valueUnavailable");
		},

		availabilityState(available) {
			return available ? "Success" : "Error";
		},

		sourceDistance(distance) {
			return typeof distance === "number" ? this.getText("sourceDistance", [distance.toFixed(3)]) : "";
		},

		backendStateText(state) {
			return this.getText({ online: "statusOnline", offline: "statusOffline" }[state] || "statusChecking");
		}
	};
});
