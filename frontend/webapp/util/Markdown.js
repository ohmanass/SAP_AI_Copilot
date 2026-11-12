sap.ui.define([], () => {
	"use strict";

	/**
	 * Minimal, safe Markdown renderer for assistant answers.
	 *
	 * Security model: every piece of text is HTML-escaped BEFORE any markup
	 * is generated, so only the tags produced here can appear in the output.
	 * The result is additionally passed through the UI5 HTML sanitizer
	 * (core:HTML sanitizeContent="true").
	 *
	 * Supported: headings, paragraphs, bold, italic, inline code, fenced code,
	 * nested ordered/unordered lists, tables, blockquotes, horizontal rules,
	 * and http(s) links.
	 */

	const LIST_ITEM = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;
	const TABLE_SEPARATOR = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;

	function escapeHtml(text) {
		return String(text)
			.replace(/&/g, "&amp;")
			.replace(/</g, "&lt;")
			.replace(/>/g, "&gt;")
			.replace(/"/g, "&quot;")
			.replace(/'/g, "&#39;");
	}

	function renderInline(text) {
		const codeSpans = [];

		let html = escapeHtml(text).replace(/`([^`]+)`/g, (match, code) => {
			codeSpans.push(`<code>${code}</code>`);
			return `\u0000${codeSpans.length - 1}\u0000`;
		});

		html = html
			.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
				'<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
			.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
			.replace(/__(.+?)__/g, "<strong>$1</strong>")
			.replace(/(^|[^*\w])\*(?!\s)([^*]+?)\*(?!\*)/g, "$1<em>$2</em>");

		return html.replace(/\u0000(\d+)\u0000/g, (match, index) => codeSpans[Number(index)]);
	}

	function splitRow(line) {
		return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim());
	}

	function renderTable(lines) {
		const header = splitRow(lines[0]);
		const rows = lines.slice(2).map(splitRow);

		const head = header.map((cell) => `<th>${renderInline(cell)}</th>`).join("");
		const body = rows.map((row) =>
			"<tr>" + header.map((_, i) => `<td>${renderInline(row[i] || "")}</td>`).join("") + "</tr>"
		).join("");

		return `<div class="cpTableWrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
	}

	function renderList(lines) {
		const root = { indent: -1, children: [] };
		const stack = [root];
		let last = null;

		lines.forEach((line) => {
			const match = LIST_ITEM.exec(line);

			if (!match) {
				// Continuation of the previous item
				if (last && line.trim()) {
					last.text += "\n" + line.trim();
				}
				return;
			}

			const indent = match[1].replace(/\t/g, "    ").length;
			while (stack.length > 1 && indent <= stack[stack.length - 1].indent) {
				stack.pop();
			}

			const marker = match[2];
			last = {
				indent,
				ordered: /\d/.test(marker),
				start: parseInt(marker, 10),
				text: match[3],
				children: []
			};
			stack[stack.length - 1].children.push(last);
			stack.push(last);
		});

		const render = (items) => {
			if (!items.length) {
				return "";
			}
			const ordered = items[0].ordered;
			const tag = ordered ? "ol" : "ul";
			const start = ordered && items[0].start > 1 ? ` start="${items[0].start}"` : "";
			const content = items.map((item) =>
				`<li>${item.text.split("\n").map(renderInline).join("<br>")}${render(item.children)}</li>`
			).join("");
			return `<${tag}${start}>${content}</${tag}>`;
		};

		return render(root.children);
	}

	function render(markdown) {
		const lines = String(markdown || "").replace(/\r\n?/g, "\n").split("\n");
		const out = [];
		let i = 0;

		while (i < lines.length) {
			const line = lines[i];
			const trimmed = line.trim();

			if (!trimmed) {
				i++;
				continue;
			}

			// Fenced code block
			const fence = /^```\s*([\w+-]*)/.exec(trimmed);
			if (fence) {
				const code = [];
				i++;
				while (i < lines.length && !lines[i].trim().startsWith("```")) {
					code.push(lines[i]);
					i++;
				}
				i++; // closing fence
				const language = fence[1] ? `<span class="cpCodeLang">${escapeHtml(fence[1])}</span>` : "";
				out.push(`<pre>${language}<code>${escapeHtml(code.join("\n"))}</code></pre>`);
				continue;
			}

			// Heading
			const heading = /^(#{1,6})\s+(.*)$/.exec(trimmed);
			if (heading) {
				const level = Math.min(heading[1].length + 2, 6); // h3..h6 inside chat bubbles
				out.push(`<h${level}>${renderInline(heading[2].replace(/#+\s*$/, ""))}</h${level}>`);
				i++;
				continue;
			}

			// Horizontal rule
			if (/^([-*_])(\s*\1){2,}$/.test(trimmed)) {
				out.push("<hr>");
				i++;
				continue;
			}

			// Table
			if (trimmed.includes("|") && i + 1 < lines.length && TABLE_SEPARATOR.test(lines[i + 1])) {
				const table = [];
				while (i < lines.length && lines[i].trim().includes("|")) {
					table.push(lines[i]);
					i++;
				}
				out.push(renderTable(table));
				continue;
			}

			// Blockquote
			if (trimmed.startsWith(">")) {
				const quote = [];
				while (i < lines.length && lines[i].trim().startsWith(">")) {
					quote.push(lines[i].trim().replace(/^>\s?/, ""));
					i++;
				}
				out.push(`<blockquote>${render(quote.join("\n")).replace(/^<div class="cpMarkdown">|<\/div>$/g, "")}</blockquote>`);
				continue;
			}

			// List (items may be separated by blank lines and contain indented continuations)
			if (LIST_ITEM.test(line)) {
				const block = [];
				while (i < lines.length) {
					const current = lines[i];
					if (LIST_ITEM.test(current) || (/^\s+\S/.test(current) && block.length)) {
						block.push(current);
						i++;
					} else if (!current.trim()) {
						let next = i + 1;
						while (next < lines.length && !lines[next].trim()) {
							next++;
						}
						if (next < lines.length && (LIST_ITEM.test(lines[next]) || /^\s+\S/.test(lines[next]))) {
							i = next;
						} else {
							break;
						}
					} else {
						break;
					}
				}
				out.push(renderList(block));
				continue;
			}

			// Paragraph
			const paragraph = [];
			while (i < lines.length && lines[i].trim() && !LIST_ITEM.test(lines[i]) &&
				!/^(#{1,6}\s|```|>)/.test(lines[i].trim())) {
				paragraph.push(lines[i].trim());
				i++;
			}
			out.push(`<p>${paragraph.map(renderInline).join("<br>")}</p>`);
		}

		return `<div class="cpMarkdown">${out.join("")}</div>`;
	}

	return { render, escapeHtml };
});
