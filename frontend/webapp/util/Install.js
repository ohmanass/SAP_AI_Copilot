sap.ui.define([
	"sap/base/Log"
], (Log) => {
	"use strict";

	/**
	 * Installation as an app (PWA): service worker (sw.js, offline page only) and
	 * the browser's install prompt, offered by the "Install the app" button.
	 *
	 * start() runs at startup (boot.js): the browser sends "beforeinstallprompt"
	 * once, early, and only to pages listening for it. Chromium browsers (Chrome,
	 * Edge, Android) send it; Safari installs from its Share menu ("Add to Home Screen").
	 */

	let deferredPrompt = null;
	const listeners = new Set();

	const notify = () => listeners.forEach((listener) => listener(deferredPrompt !== null));

	return {

		start() {
			if ("serviceWorker" in navigator && window.isSecureContext) {
				// Registered once the page is loaded (boot.js may run after the "load" event)
				const register = () => navigator.serviceWorker.register("sw.js").catch((error) => {
					Log.warning("Service worker not registered", error.message, "com.sapaicopilot.app.Install");
				});
				if (document.readyState === "complete") {
					register();
				} else {
					window.addEventListener("load", register, { once: true });
				}
			}
			window.addEventListener("beforeinstallprompt", (event) => {
				event.preventDefault(); // our own button instead of the browser's mini bar
				deferredPrompt = event;
				notify();
			});
			window.addEventListener("appinstalled", () => {
				deferredPrompt = null;
				notify();
			});
		},

		/** listener(canInstall) is called now and whenever installing becomes (im)possible. */
		onChange(listener) {
			listeners.add(listener);
			listener(deferredPrompt !== null);
		},

		/** Shows the browser's install dialog; resolves with true when the user accepted. */
		async install() {
			if (!deferredPrompt) {
				return false;
			}
			const prompt = deferredPrompt;
			deferredPrompt = null; // a prompt can be shown once only
			notify();
			await prompt.prompt();
			const choice = await prompt.userChoice;
			return choice.outcome === "accepted";
		}
	};
});
