sap.ui.define([
	"sap/ui/core/format/DateFormat",
	"./Markdown",
	"./DataTable"
], (DateFormat, Markdown, DataTable) => {
	"use strict";

	/**
	 * Export of a conversation, built in the browser from what the chat shows:
	 *   - Markdown (.md file), for a ticket, a wiki or an email;
	 *   - PDF, through the browser print dialog ("Save as PDF"): a light,
	 *     printable page built in a hidden frame, no library and no server call.
	 *
	 * Answers go through the same safe Markdown renderer as the chat (text is
	 * escaped before any markup is generated); SAP data tables are exported
	 * with their formatted values.
	 */

	const dateTime = DateFormat.getDateTimeInstance({ style: "medium" });
	const FORBIDDEN_FILE_CHARS = /[\\/:*?"<>|\u0000-\u001f]+/g;

	function formatTime(value) {
		const date = value ? new Date(value) : null;
		return date && !isNaN(date) ? dateTime.format(date) : "";
	}

	function fileName(title, extension) {
		const base = String(title || "conversation").replace(FORBIDDEN_FILE_CHARS, " ").replace(/\s+/g, " ").trim()
			.slice(0, 80).replace(/[.\s]+$/, "") || "conversation";
		return `${base}.${extension}`;
	}

	/** Messages to export: questions and answers (errors and empty answers are left out). */
	function exportedMessages(conversation) {
		return conversation.messages.filter((message) =>
			(message.role === "user" || message.role === "assistant") && (message.content || "").trim());
	}

	// ------------------------------------------------------------------
	// Markdown
	// ------------------------------------------------------------------

	const cell = (value) => String(value === null || value === undefined ? "" : value)
		.replace(/\|/g, "\\|").replace(/\r?\n/g, " ");

	function tableMarkdown(table) {
		const lines = [`**${table.title}**` + (table.meta.length ? ` (${table.meta.join(" · ")})` : "")];
		if (table.attributes.length) {
			lines.push("", ...table.attributes.map((a) => `- ${a.label}: ${a.value}`));
		}
		if (table.rows.length) {
			lines.push("",
				`| ${table.columns.map(cell).join(" | ")} |`,
				`| ${table.columns.map(() => "---").join(" | ")} |`,
				...table.rows.map((row) => `| ${row.map(cell).join(" | ")} |`));
		}
		if (table.total) {
			lines.push("", `_${table.total}_`);
		}
		return lines.join("\n");
	}

	function toMarkdown(controller, conversation) {
		const t = (key, args) => controller.getText(key, args);
		const out = [`# ${conversation.title}`, ""];
		const details = [t("exportExported", [formatTime(new Date())])];
		if (conversation.module) {
			details.push(t("exportWorkspace", [conversation.module]));
		}
		if (conversation.model) {
			details.push(t("exportModel", [conversation.model]));
		}
		out.push(`_${details.join(" · ")}_`, "");

		exportedMessages(conversation).forEach((message) => {
			const isUser = message.role === "user";
			out.push("---", "", `### ${isUser ? t("you") : t("assistant")} · ${formatTime(message.time)}`, "");
			if ((message.attachments || []).length) {
				out.push(`${t("exportAttachments")} ${message.attachments.map((a) => a.name).join(", ")}`, "");
			}
			if (isUser) {
				// A question is shown as written: quoted, so its own Markdown does not change the page
				out.push(message.content.split("\n").map((line) => `> ${line}`).join("\n"), "");
				return;
			}
			out.push(message.content.trim(), "");
			(message.tables || []).forEach((table) => out.push(tableMarkdown(DataTable.toText(controller, table)), ""));
			if ((message.tools || []).length) {
				out.push(`${t("exportTools")} ${message.tools.map((tool) => tool.label).join(", ")}`, "");
			}
			if ((message.sources || []).length) {
				out.push(`${t("exportSources")} ${message.sources.map((source) => source.label).join(", ")}`, "");
			}
		});
		out.push("---", "", `_${t("exportDisclaimer")}_`, "");
		return out.join("\n");
	}

	function download(content, name, type) {
		const url = URL.createObjectURL(new Blob([content], { type }));
		const link = document.createElement("a");
		link.href = url;
		link.download = name;
		document.body.appendChild(link);
		link.click();
		link.remove();
		setTimeout(() => URL.revokeObjectURL(url), 10000);
	}

	// ------------------------------------------------------------------
	// PDF (print)
	// ------------------------------------------------------------------

	const PRINT_STYLE = `
		@page { margin: 16mm 14mm; }
		* { box-sizing: border-box; }
		body { margin: 0; color: #1d2d3e; background: #fff; font: 10.5pt/1.5 "72", "Segoe UI", Arial, sans-serif; }
		h1 { margin: 0 0 2pt; font-size: 17pt; }
		.meta { margin: 0 0 14pt; color: #5b738b; font-size: 9pt; }
		.msg { padding: 9pt 0; border-top: 1px solid #d9e1ea; break-inside: auto; }
		.who { margin-bottom: 4pt; font-weight: 700; font-size: 9.5pt; color: #0a6ed1; }
		.who.user { color: #354a5f; }
		.who span { font-weight: 400; color: #5b738b; }
		.question { margin: 0; padding: 6pt 9pt; border-left: 3px solid #a9b4be; background: #f5f6f7; white-space: pre-wrap; }
		.note { margin: 5pt 0 0; color: #5b738b; font-size: 8.5pt; }
		p { margin: 0 0 6pt; }
		ul, ol { margin: 0 0 6pt; padding-left: 16pt; }
		h3, h4, h5, h6 { margin: 8pt 0 4pt; font-size: 11pt; }
		code { font-family: Consolas, "Courier New", monospace; font-size: 9pt; background: #f0f2f4; padding: 0 2pt; border-radius: 2pt; }
		pre { margin: 4pt 0 8pt; padding: 7pt 9pt; border: 1px solid #d9e1ea; border-radius: 4pt; background: #f7f8fa;
			white-space: pre-wrap; word-break: break-word; break-inside: avoid; }
		pre code { background: none; padding: 0; }
		.cpCodeLang { display: block; margin-bottom: 3pt; color: #5b738b; font-size: 8pt; }
		.cpCopyCode { display: none; }
		blockquote { margin: 0 0 6pt; padding-left: 8pt; border-left: 3px solid #d9e1ea; color: #475e75; }
		table { width: 100%; margin: 4pt 0 8pt; border-collapse: collapse; font-size: 8.5pt; }
		th, td { padding: 3pt 5pt; border: 1px solid #d9e1ea; text-align: left; vertical-align: top; }
		th { background: #f0f2f4; }
		tr { break-inside: avoid; }
		.dataTitle { margin: 8pt 0 2pt; font-weight: 700; }
		.dataTitle span { font-weight: 400; color: #5b738b; }
		.attributes { margin: 0 0 4pt; font-size: 9pt; }
		.footer { margin-top: 12pt; padding-top: 6pt; border-top: 1px solid #d9e1ea; color: #5b738b; font-size: 8.5pt; }
	`;

	function tableHtml(table) {
		const esc = Markdown.escapeHtml;
		let html = `<div class="dataTitle">${esc(table.title)}` +
			(table.meta.length ? ` <span>(${esc(table.meta.join(" · "))})</span>` : "") + "</div>";
		if (table.attributes.length) {
			html += `<div class="attributes">${table.attributes.map((a) =>
				`<b>${esc(a.label)}</b>: ${esc(a.value)}`).join(" · ")}</div>`;
		}
		if (table.rows.length) {
			html += `<table><thead><tr>${table.columns.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead><tbody>` +
				table.rows.map((row) => `<tr>${row.map((v) => `<td>${esc(v)}</td>`).join("")}</tr>`).join("") +
				"</tbody></table>";
		}
		if (table.total) {
			html += `<p class="note">${esc(table.total)}</p>`;
		}
		return html;
	}

	function toHtml(controller, conversation) {
		const t = (key, args) => controller.getText(key, args);
		const esc = Markdown.escapeHtml;
		const details = [t("exportExported", [formatTime(new Date())])];
		if (conversation.module) {
			details.push(t("exportWorkspace", [conversation.module]));
		}
		if (conversation.model) {
			details.push(t("exportModel", [conversation.model]));
		}

		const messages = exportedMessages(conversation).map((message) => {
			const isUser = message.role === "user";
			let html = `<div class="who${isUser ? " user" : ""}">${esc(isUser ? t("you") : t("assistant"))} ` +
				`<span>· ${esc(formatTime(message.time))}</span></div>`;
			if (isUser) {
				html += `<p class="question">${esc(message.content)}</p>`;
			} else {
				html += Markdown.render(message.content);
				html += (message.tables || []).map((table) => tableHtml(DataTable.toText(controller, table))).join("");
			}
			const notes = [];
			if ((message.attachments || []).length) {
				notes.push(`${t("exportAttachments")} ${message.attachments.map((a) => a.name).join(", ")}`);
			}
			if (!isUser && (message.tools || []).length) {
				notes.push(`${t("exportTools")} ${message.tools.map((tool) => tool.label).join(", ")}`);
			}
			if (!isUser && (message.sources || []).length) {
				notes.push(`${t("exportSources")} ${message.sources.map((source) => source.label).join(", ")}`);
			}
			html += notes.map((note) => `<p class="note">${esc(note)}</p>`).join("");
			return `<section class="msg">${html}</section>`;
		}).join("");

		return "<!DOCTYPE html><html><head><meta charset=\"utf-8\">" +
			`<title>${esc(conversation.title)}</title><style>${PRINT_STYLE}</style></head><body>` +
			`<h1>${esc(conversation.title)}</h1><p class="meta">${esc(details.join(" · "))}</p>` +
			messages +
			`<p class="footer">${esc(t("exportDisclaimer"))}</p></body></html>`;
	}

	/** Prints the conversation from a hidden frame: the browser offers "Save as PDF". */
	function print(controller, conversation) {
		document.querySelectorAll(".cpPrintFrame").forEach((old) => old.remove());
		const frame = document.createElement("iframe");
		frame.setAttribute("aria-hidden", "true");
		// No script can run in the printed page (its content is already escaped)
		frame.setAttribute("sandbox", "allow-same-origin allow-modals");
		frame.className = "cpPrintFrame";
		frame.style.cssText = "position:fixed;right:0;bottom:0;width:0;height:0;border:0;visibility:hidden";
		document.body.appendChild(frame);

		const doc = frame.contentDocument;
		doc.open();
		doc.write(toHtml(controller, conversation));
		doc.close();

		const printNow = () => {
			frame.contentWindow.addEventListener("afterprint", () => setTimeout(() => frame.remove(), 0));
			frame.contentWindow.focus();
			frame.contentWindow.print();
		};
		// Let the frame lay out (fonts, tables) before printing
		setTimeout(printNow, 250);
	}

	return {
		toMarkdown,
		toHtml,

		downloadMarkdown(controller, conversation) {
			download(toMarkdown(controller, conversation), fileName(conversation.title, "md"), "text/markdown;charset=utf-8");
		},

		print
	};
});
