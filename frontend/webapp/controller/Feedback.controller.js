sap.ui.define([
	"./BaseController",
	"sap/ui/model/json/JSONModel",
	"sap/m/MessageToast",
	"sap/ui/core/format/DateFormat"
], (BaseController, JSONModel, MessageToast, DateFormat) => {
	"use strict";

	const dateTime = DateFormat.getDateTimeInstance({ style: "medium" });

	/**
	 * Review of the feedback on the assistant's answers: statistics, latest
	 * feedback (every user's for a reviewer, the user's own otherwise), and a
	 * 👎 answer copied as a case of the evaluation suite (evals/agent_eval.py).
	 */
	return BaseController.extend("com.sapaicopilot.app.controller.Feedback", {

		onInit() {
			this.getView().setModel(new JSONModel({ busy: false, error: "", filter: "all", data: null }), "fb");
			this.getRouter().getRoute("feedback").attachPatternMatched(this._onRouteMatched, this);
		},

		_onRouteMatched() {
			this.setSection("feedback");
			this._load();
		},

		onRefresh() {
			this._load();
		},

		onFilterChange() {
			this._load();
		},

		async _load() {
			const model = this.getView().getModel("fb");
			const filter = model.getProperty("/filter");
			model.setProperty("/busy", true);
			model.setProperty("/error", "");
			try {
				model.setProperty("/data", await this.getService().getFeedback(filter === "all" ? null : Number(filter)));
			} catch (error) {
				model.setProperty("/data", null);
				model.setProperty("/error", `${this.getText("feedbackLoadError")} ${this.describeError(error)}`);
			} finally {
				model.setProperty("/busy", false);
			}
		},

		formatMeta(module, tools, updatedAt, user) {
			const parts = [module ? module.toUpperCase() : this.getText("feedbackGeneral")];
			if (tools && tools.length) {
				parts.push(tools.map((name) => this._toolLabel(name)).join(", "));
			}
			const date = updatedAt ? new Date(updatedAt) : null;
			if (date && !isNaN(date)) {
				parts.push(dateTime.format(date));
			}
			if (user) {
				parts.push(user);
			}
			return parts.join("  ·  ");
		},

		_toolLabel(name) {
			const bundle = this.getOwnerComponent().getModel("i18n").getResourceBundle();
			return bundle.hasText(`tool.${name}`) ? bundle.getText(`tool.${name}`) : name;
		},

		onOpenConversation(event) {
			const sessionId = event.getSource().getBindingContext("fb").getProperty("session_id");
			this.getRouter().navTo("conversation", { sessionId });
		},

		/**
		 * Python snippet for evals/agent_eval.py (CASES): the question, its
		 * workspace and the tools the answer used, with the comment as notes.
		 * The expectations are to be completed by hand: the tools used may be wrong.
		 */
		async onCopyEvalCase(event) {
			const item = event.getSource().getBindingContext("fb").getObject();
			// JSON string literals are valid Python string literals
			const str = (value) => JSON.stringify(String(value || ""));
			const tools = item.tools_used || [];
			const lines = [
				`    # From feedback ${item.rating === 1 ? "👍" : "👎"} of ${item.updated_at}: complete the expectations`,
				`    Case(${str(`feedback-${item.message_id}`)}, ${str(item.question)},` +
					(item.module ? ` module=${str(item.module)},` : ""),
				`         must_call=(${tools.map(str).join(", ")}${tools.length === 1 ? "," : ""}), answer_contains=(),`,
				`         notes=${str(item.comment || (item.rating === 1 ? "answer judged useful" : "answer judged not useful"))}),`
			];
			try {
				await navigator.clipboard.writeText(lines.join("\n") + "\n");
				MessageToast.show(this.getText("feedbackCaseCopied"));
			} catch (error) {
				MessageToast.show(this.getText("copyFailed"));
			}
		}
	});
});
