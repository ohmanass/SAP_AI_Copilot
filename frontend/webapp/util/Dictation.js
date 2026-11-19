sap.ui.define([], () => {
	"use strict";

	/**
	 * Dictation: speech to text with the browser's Web Speech API (no AI call of the app).
	 *
	 * Supported by Chrome, Edge and Safari (iPhone included), not by Firefox: the
	 * microphone button is shown only where it works. The browser sends the audio to
	 * its own speech service (Google for Chrome, Microsoft for Edge, Apple for Safari).
	 *
	 * The text is reported as it is recognised: the final part (stable) and the
	 * interim part (may still change). Nothing is sent to the assistant: the user
	 * reads the text and sends it.
	 */

	/** The browser's recognition class, looked up when used (standard name first, then Chrome's prefixed one). */
	const recognitionClass = () => window.SpeechRecognition || window.webkitSpeechRecognition;

	// Errors that end the dictation without anything to tell the user
	const SILENT_ERRORS = new Set(["aborted"]);

	/**
	 * @param {object} handlers
	 *   language()          BCP 47 tag of the language spoken (the interface language)
	 *   onText(final, interim)  recognised text since the start (final part, interim part)
	 *   onState(listening)  dictation started or ended
	 *   onError(code)       "not-allowed", "no-speech", "audio-capture", "network"...
	 */
	function create(handlers) {
		let recognition = null;
		let finalText = "";

		const stop = () => {
			if (recognition) {
				recognition.stop(); // "end" follows, with the last results
			}
		};

		const start = () => {
			const Recognition = recognitionClass();
			if (!Recognition || recognition) {
				return;
			}
			finalText = "";
			recognition = new Recognition();
			recognition.lang = handlers.language();
			recognition.continuous = true;      // until the user stops it (or a long silence)
			recognition.interimResults = true;  // the text appears while speaking
			recognition.maxAlternatives = 1;

			recognition.onresult = (event) => {
				let interim = "";
				for (let i = event.resultIndex; i < event.results.length; i++) {
					const transcript = event.results[i][0].transcript;
					if (event.results[i].isFinal) {
						finalText += transcript;
					} else {
						interim += transcript;
					}
				}
				handlers.onText(finalText, interim);
			};
			recognition.onerror = (event) => {
				if (!SILENT_ERRORS.has(event.error)) {
					handlers.onError(event.error);
				}
			};
			recognition.onend = () => {
				recognition = null;
				handlers.onText(finalText, "");
				handlers.onState(false);
			};

			try {
				recognition.start();
				handlers.onState(true);
			} catch (error) {
				recognition = null;
				handlers.onError("start-failed");
			}
		};

		return {
			start,
			stop,
			toggle() {
				if (recognition) {
					stop();
				} else {
					start();
				}
			},
			isListening: () => recognition !== null
		};
	}

	return {
		/** Dictation is possible in this browser (API present, secure page for the microphone). */
		isSupported: () => !!recognitionClass() && window.isSecureContext,
		create
	};
});
