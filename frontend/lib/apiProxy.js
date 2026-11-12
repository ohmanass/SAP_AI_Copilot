/**
 * UI5 Tooling custom middleware: forwards /api/* to the local FastAPI backend.
 *
 * In production the SAP Application Router plays this role, so the frontend
 * always calls the relative path "api/". Responses are piped as they arrive,
 * which keeps the NDJSON streaming of /api/chat/stream intact.
 *
 * Target: API_PROXY_TARGET env var, default http://127.0.0.1:8000
 */
const http = require("http");

module.exports = function ({ log }) {
	const target = new URL(process.env.API_PROXY_TARGET || "http://127.0.0.1:8000");

	return function apiProxy(req, res, next) {
		if (!req.url.startsWith("/api/")) {
			return next();
		}

		const headers = Object.assign({}, req.headers, { host: target.host });
		const upstream = http.request({
			protocol: target.protocol,
			hostname: target.hostname,
			port: target.port,
			method: req.method,
			path: req.url,
			headers
		}, (response) => {
			res.writeHead(response.statusCode, response.headers);
			response.pipe(res);
		});

		upstream.on("error", (error) => {
			log.warn(`API proxy: ${error.message}`);
			if (!res.headersSent) {
				res.writeHead(502, { "Content-Type": "application/json" });
			}
			res.end(JSON.stringify({ detail: `Backend unreachable at ${target.origin}` }));
		});

		// Abort the backend request if the browser disconnects (Stop button)
		res.on("close", () => {
			if (!res.writableEnded) {
				upstream.destroy();
			}
		});

		req.pipe(upstream);
	};
};
