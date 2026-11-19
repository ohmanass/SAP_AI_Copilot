/* global QUnit */
sap.ui.define([
	"com/sapaicopilot/app/util/Dictation"
], (Dictation) => {
	"use strict";

	/** Fake of the browser's SpeechRecognition: the test emits its events. */
	class FakeRecognition {
		constructor() {
			FakeRecognition.last = this;
		}

		start() {
			this.started = true;
		}

		stop() {
			this.onend();
		}

		result(parts) {
			this.onresult({
				resultIndex: 0,
				results: parts.map(([transcript, isFinal]) => Object.assign([{ transcript }], { isFinal }))
			});
		}
	}

	QUnit.module("util/Dictation", {
		beforeEach() {
			this.original = window.SpeechRecognition;
			window.SpeechRecognition = FakeRecognition;
			this.events = [];
			this.dictation = Dictation.create({
				language: () => "fr",
				onText: (finalText, interim) => this.events.push(["text", finalText, interim]),
				onState: (listening) => this.events.push(["state", listening]),
				onError: (code) => this.events.push(["error", code])
			});
		},
		afterEach() {
			window.SpeechRecognition = this.original;
		}
	});

	QUnit.test("Interim then final text, in the interface language", function (assert) {
		this.dictation.start();
		const recognition = FakeRecognition.last;
		assert.ok(recognition.started && recognition.lang === "fr", "started in French");
		assert.ok(recognition.continuous && recognition.interimResults, "text shown while speaking");
		assert.ok(this.dictation.isListening());

		recognition.result([["montre les", false]]);
		recognition.result([["montre les commandes", true], [" en retard", false]]);
		this.dictation.stop();

		assert.deepEqual(this.events, [
			["state", true],
			["text", "", "montre les"],
			["text", "montre les commandes", " en retard"],
			["text", "montre les commandes", ""],
			["state", false]
		]);
		assert.notOk(this.dictation.isListening());
	});

	QUnit.test("Errors are reported, except a stop by the app", function (assert) {
		this.dictation.start();
		FakeRecognition.last.onerror({ error: "aborted" });
		FakeRecognition.last.onerror({ error: "not-allowed" });
		assert.deepEqual(this.events.filter((event) => event[0] === "error"), [["error", "not-allowed"]]);
	});

	QUnit.test("Toggle starts and stops", function (assert) {
		this.dictation.toggle();
		assert.ok(this.dictation.isListening());
		this.dictation.toggle();
		assert.notOk(this.dictation.isListening());
	});

	QUnit.test("Not offered without the browser API", (assert) => {
		const standard = window.SpeechRecognition;
		const prefixed = window.webkitSpeechRecognition;
		window.SpeechRecognition = undefined;
		window.webkitSpeechRecognition = undefined;
		try {
			assert.notOk(Dictation.isSupported());
		} finally {
			window.SpeechRecognition = standard;
			window.webkitSpeechRecognition = prefixed;
		}
	});
});
