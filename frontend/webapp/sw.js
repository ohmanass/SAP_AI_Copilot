/*
 * Service worker of the installed app (PWA).
 *
 * On purpose minimal for an enterprise app: no SAP data, no API answer and no
 * page of the app is ever kept on the device. Requests go to the network as if
 * there were no service worker; only when a page cannot be loaded at all (no
 * network) the offline page is shown instead of the browser error.
 *
 * Change VERSION when the offline page changes: the old cache is then removed.
 */
const VERSION = "2";
const CACHE = `sap-ai-copilot-offline-${VERSION}`;
const OFFLINE_PAGE = "offline.html";
// Files of the offline page itself
const OFFLINE_FILES = [OFFLINE_PAGE, "offline.js", "img/logo.svg"];

self.addEventListener("install", (event) => {
	event.waitUntil(
		caches.open(CACHE)
			.then((cache) => cache.addAll(OFFLINE_FILES))
			.then(() => self.skipWaiting())
	);
});

self.addEventListener("activate", (event) => {
	event.waitUntil(
		caches.keys()
			.then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
			.then(() => self.clients.claim())
	);
});

self.addEventListener("fetch", (event) => {
	const request = event.request;
	if (request.mode === "navigate") {
		// Page loads: network (SAP login redirects included), offline page only when it fails
		event.respondWith(fetch(request).catch(() => caches.match(OFFLINE_PAGE)));
	} else if (OFFLINE_FILES.some((file) => new URL(request.url).pathname.endsWith(`/${file}`))) {
		// Script and logo of the offline page
		event.respondWith(fetch(request).catch(() => caches.match(request)));
	}
	// Everything else (API, scripts, styles, SAPUI5): untouched, straight to the network
});
