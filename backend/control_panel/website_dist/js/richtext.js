/* ============================================================
   AI4S-Benchmark · Rich text
   Proposal fields arrive from the control plane as plain text
   that authors write like a GitHub Discussion: paragraphs,
   lists, links, a little Markdown and LaTeX. This module turns
   that text into safe HTML and renders the math with KaTeX.

   Safety: every character is HTML-escaped before any markup is
   applied, so untrusted proposal text can never inject HTML.
   Math is lifted out before Markdown runs so `_`, `*` and `\`
   inside formulas are never mangled.
   ============================================================ */

const KATEX_VERSION = "0.16.22";
const KATEX_BASE = `https://cdn.jsdelivr.net/npm/katex@${KATEX_VERSION}/dist`;

const MATH_DELIMITERS = [
  { left: "$$", right: "$$", display: true },
  { left: "\\[", right: "\\]", display: true },
  { left: "\\(", right: "\\)", display: false },
  { left: "$", right: "$", display: false },
  { left: "\\begin{equation}", right: "\\end{equation}", display: true },
  { left: "\\begin{equation*}", right: "\\end{equation*}", display: true },
  { left: "\\begin{align}", right: "\\end{align}", display: true },
  { left: "\\begin{align*}", right: "\\end{align*}", display: true },
  { left: "\\begin{gather}", right: "\\end{gather}", display: true },
  { left: "\\begin{gather*}", right: "\\end{gather*}", display: true },
  { left: "\\begin{alignat}", right: "\\end{alignat}", display: true },
  { left: "\\begin{multline}", right: "\\end{multline}", display: true },
];

function escapeHTML(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

/* ---- Math detection ----------------------------------------
   A lone "$" in prose ("costs $5") must not start a formula, so
   inline math needs a non-space right after the opening "$" and
   right before the closing one, on a single line. Display math
   ($$…$$, \[…\]) may span lines. */

const MATH_PATTERN =
  /\\begin\{(equation\*?|align\*?|gather\*?|alignat|multline)\}[\s\S]+?\\end\{\1\}|\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\]|\\\(([\s\S]+?)\\\)|(?<![\\$\w])\$(?=\S)((?:\\.|[^$\n])+?)(?<=\S)\$(?![\w$])/g;

export function hasMath(text) {
  MATH_PATTERN.lastIndex = 0;
  return MATH_PATTERN.test(String(text ?? ""));
}

function liftMath(text, slots = []) {
  const lifted = text.replace(MATH_PATTERN, (match) => {
    slots.push(match);
    return `\uE000${slots.length - 1}\uE001`;
  });
  return { lifted, slots };
}

function restoreMath(html, slots) {
  return html.replace(/\uE000(\d+)\uE001/g, (_, i) => escapeHTML(slots[Number(i)]));
}

/* ---- Inline markup ----------------------------------------- */

const URL_PATTERN = /\bhttps?:\/\/[^\s<>()]+[^\s<>().,;:!?'"\]]/g;

function inline(escaped) {
  let out = escaped;

  // `code`
  out = out.replace(/`([^`\n]+)`/g, (_, code) => `<code>${code}</code>`);

  // [text](url) — only http(s) targets
  out = out.replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, (_, label, url) => link(url, label));

  // bare URLs (skip ones already inside an href/anchor from the step above)
  out = out.replace(URL_PATTERN, (url, offset, whole) => {
    const before = whole.slice(Math.max(0, offset - 6), offset);
    if (before.endsWith('href="') || before.endsWith('">')) return url;
    return link(url, url);
  });

  // **bold** and *emphasis*. Underscore emphasis is deliberately not
  // supported: subscripts like n_i and file_names are common in science.
  out = out.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
  out = out.replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,;:!?]|$)/g, "$1<em>$2</em>");

  return out;
}

function link(url, label) {
  const safe = url.replaceAll("&amp;", "&"); // the URL was escaped once already
  return `<a href="${escapeHTML(safe)}" target="_blank" rel="noopener">${label}</a>`;
}

/* ---- Block structure --------------------------------------- */

const BULLET = /^\s*[-*•]\s+(.*)$/;
const NUMBERED = /^\s*(\d+)[.)]\s+(.*)$/;
const HEADING = /^\s*(#{1,4})\s+(.*)$/;
const FENCE = /^\s*```/;

function blocks(lines) {
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];

    if (line.trim() === "") { i++; continue; }

    if (FENCE.test(line)) {
      const code = [];
      i++;
      while (i < lines.length && !FENCE.test(lines[i])) code.push(lines[i++]);
      i++; // closing fence
      out.push(`<pre><code>${code.join("\n")}</code></pre>`);
      continue;
    }

    const h = line.match(HEADING);
    if (h) {
      const level = h[1].length <= 2 ? 3 : 4; // #/## → h3, ###/#### → h4 (inside a page section)
      out.push(`<h${level}>${inline(h[2].trim())}</h${level}>`);
      i++;
      continue;
    }

    if (BULLET.test(line) || NUMBERED.test(line)) {
      const ordered = NUMBERED.test(line);
      const re = ordered ? NUMBERED : BULLET;
      const items = [];
      let start = ordered ? Number(line.match(NUMBERED)[1]) : 1;
      while (i < lines.length && re.test(lines[i])) {
        const m = lines[i].match(re);
        let body = ordered ? m[2] : m[1];
        i++;
        // continuation lines belong to the item until a blank line or a new marker
        while (i < lines.length && lines[i].trim() !== "" && !re.test(lines[i]) && !BULLET.test(lines[i]) && !NUMBERED.test(lines[i])) {
          body += `<br>${lines[i].trim()}`;
          i++;
        }
        items.push(`<li>${inline(body)}</li>`);
        // Authors often separate items with a blank line; keep them in one list.
        let j = i;
        while (j < lines.length && lines[j].trim() === "") j++;
        if (j < lines.length && re.test(lines[j])) i = j;
      }
      const startAttr = ordered && start !== 1 ? ` start="${start}"` : "";
      out.push(ordered ? `<ol${startAttr}>${items.join("")}</ol>` : `<ul>${items.join("")}</ul>`);
      continue;
    }

    if (/^\s*(---|\*\*\*|___)\s*$/.test(line)) {
      out.push("<hr>");
      i++;
      continue;
    }

    // Paragraph: consecutive non-blank lines. A short label line such as
    // "Background" followed by prose becomes a subheading, matching how
    // authors structure Discussion posts without Markdown syntax.
    const para = [];
    while (i < lines.length && lines[i].trim() !== "" && !FENCE.test(lines[i]) && !HEADING.test(lines[i]) && !BULLET.test(lines[i]) && !NUMBERED.test(lines[i])) {
      para.push(lines[i].trim());
      i++;
    }
    if (para.length > 1 && isLabel(para[0], para[1])) {
      out.push(`<h4>${inline(para[0])}</h4>`);
      para.shift();
    }
    out.push(`<p>${para.map(inline).join("<br>")}</p>`);
  }
  return out;
}

/* "Background" / "Scientific target" on a line of its own, followed by a
   sentence, is a heading the author typed without Markdown. Anything that
   reads like the start of a sentence stays inside the paragraph. */
function isLabel(line, next) {
  if (line.length > 40) return false;
  if (/[.:;,!?)\]=$]$/.test(line)) return false;
  if (/^https?:\/\//.test(line) || /[=<>{}$\\]/.test(line)) return false;
  const words = line.split(/\s+/);
  if (words.length > 4 || !/^[A-Z]/.test(line)) return false;
  return /^[A-Z0-9$\\(]/.test(next);
}

/* ---- Recovery of stripped LaTeX delimiters ------------------
   Some proposals arrive with the backslashes of \[ \] and \( \)
   lost in transit ("[" alone on a line, "(\omega)-limit"). When a
   text clearly contains TeX commands, rebuild those delimiters so
   the formulas typeset instead of showing as fragments. Text without
   any TeX command is never touched. */
const TEX_COMMAND = /\\[a-zA-Z]+/;
const MATHY_LINE = /[\\_^{}=]/;

// A "[" or "]" alone on a line is the stripped form of \[ or \]; a formula
// such as m^*/m = 1 + F_1^s/3 needs no backslash command at all.
const BRACKET_LINE = /^\s*[[\]]\s*$/m;

function recoverTeX(text) {
  if (!TEX_COMMAND.test(text) && !BRACKET_LINE.test(text)) return text;
  const lines = text.split("\n");
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i].trim();
    if (line === "[") {
      let j = i + 1;
      const body = [];
      while (j < lines.length && lines[j].trim() !== "]") body.push(lines[j++]);
      if (j < lines.length && body.some((b) => MATHY_LINE.test(b))) {
        out.push("$$" + body.join("\n") + "$$");
        i = j + 1;
        continue;
      }
    }
    if (line === "]") {
      const body = [];
      while (out.length) {
        const prev = out[out.length - 1];
        if (prev.trim() === "" || !MATHY_LINE.test(prev) || /\$\$/.test(prev) || /^[A-Z][a-z]+ [a-z]/.test(prev.trim())) break;
        body.unshift(out.pop());
      }
      if (body.length) {
        out.push("$$" + body.join("\n") + "$$");
        i++;
        continue;
      }
    }
    out.push(lines[i]);
    i++;
  }
  return out.join("\n");
}

// (\omega), (F_l^{s,a}), (m^*/m), (N\to\infty), (l=1), (P_1(\cos\theta)) → inline math.
// Runs on text whose display blocks are already lifted, so it never reaches inside them.
function recoverInlineTeX(text) {
  if (!TEX_COMMAND.test(text)) return text;
  return text.replace(/(?<![\\$\w])\(((?:[^()\s]|\([^()\s]*\)){1,48})\)(?![\w(])/g, (m, inner) => {
    if (/^https?:/.test(inner)) return m;
    if (/[\\_^]/.test(inner) || /^[a-zA-Z]\w*=[^=]+$/.test(inner)) return "\\(" + inner + "\\)";
    return m;
  });
}

/**
 * Render proposal text to HTML. Output is safe to insert with innerHTML.
 * Math stays as escaped source text; call mountMath() on the container
 * to typeset it.
 */
export function renderRich(text) {
  const source = String(text ?? "").replace(/\r\n?/g, "\n").trim();
  if (!source) return "";
  // Lift well-formed math, rebuild stripped display blocks, lift those,
  // then rebuild stripped inline math and lift again. Each pass only sees
  // text outside the formulas already found.
  const pass1 = liftMath(source);
  const pass2 = liftMath(recoverTeX(pass1.lifted), pass1.slots);
  const { lifted, slots } = liftMath(recoverInlineTeX(pass2.lifted), pass2.slots);
  const html = blocks(escapeHTML(lifted).split("\n")).join("\n");
  return restoreMath(html, slots);
}

/**
 * One-line summary of proposal text for lists (the task board). Drops
 * headings, "Background"-style label lines, list markers, link targets and
 * emphasis markers, keeps every formula (display math becomes inline), and
 * truncates at a word boundary without ever cutting a formula in half.
 * Returns safe HTML; call mountMath() on the container to typeset it.
 */
export function excerptHTML(text, max = 260) {
  const source = String(text ?? "").replace(/\r\n?/g, "\n").trim();
  if (!source) return "";
  const pass1 = liftMath(source);
  const pass2 = liftMath(recoverTeX(pass1.lifted), pass1.slots);
  const { lifted, slots } = liftMath(recoverInlineTeX(pass2.lifted), pass2.slots);

  const lines = lifted.split("\n");
  const kept = [];
  let inFence = false;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();
    if (FENCE.test(line)) { inFence = !inFence; continue; }
    if (inFence || !line || HEADING.test(line) || /^(---|\*\*\*|___)$/.test(line)) continue;
    const next = (lines.slice(i + 1).find((l) => l.trim()) ?? "").trim();
    if (next && isLabel(line, next)) continue;
    const bullet = line.match(BULLET);
    const numbered = line.match(NUMBERED);
    kept.push(bullet ? bullet[1] : numbered ? numbered[2] : line);
  }
  const plain = kept
    .join(" ")
    .replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, "$1")
    .replace(/\*\*([^*\n]+)\*\*/g, "$1")
    .replace(/`([^`\n]+)`/g, "$1")
    .replace(/(^|[\s(])\*([^*\n]+)\*/g, "$1$2")
    .replace(/\s+/g, " ")
    .trim();

  // Walk text and formula tokens so truncation never splits a formula.
  const tokens = plain.split(/(\d+)/).filter(Boolean);
  let out = "";
  let length = 0;
  let cut = false;
  for (const token of tokens) {
    const slot = token.match(/^(\d+)$/);
    if (slot) {
      const math = inlineMath(slots[Number(slot[1])]);
      const cost = Math.min(12, math.length);
      if (length + cost > max) { cut = true; break; }
      out += escapeHTML(math);
      length += cost;
      continue;
    }
    if (length + token.length <= max) {
      out += escapeHTML(token);
      length += token.length;
      continue;
    }
    const room = token.slice(0, Math.max(0, max - length));
    const atWord = room.replace(/\s+\S*$/, "");
    out += escapeHTML(atWord.length > room.length * 0.6 ? atWord : room);
    cut = true;
    break;
  }
  return cut ? `${out.trimEnd()}…` : out;
}

/* Display math shown inside a one-line summary: same formula, inline. */
function inlineMath(math) {
  const flat = String(math).replace(/\s*\n\s*/g, " ").trim();
  if (flat.startsWith("$$")) return `$${flat.slice(2, -2).trim()}$`;
  if (flat.startsWith("\\[")) return `\\(${flat.slice(2, -2).trim()}\\)`;
  if (flat.startsWith("\\begin{")) return "[formula]";
  return flat;
}

export function richBlock(text, extraClass = "") {
  const html = renderRich(text);
  return html ? `<div class="rich${extraClass ? ` ${extraClass}` : ""}">${html}</div>` : "";
}

/* ---- KaTeX (loaded only when a page actually contains math) -- */

let katexLoading = null;

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = src;
    s.async = true;
    s.onload = resolve;
    s.onerror = () => reject(new Error(`Failed to load ${src}`));
    document.head.appendChild(s);
  });
}

function loadKaTeX() {
  if (katexLoading) return katexLoading;
  katexLoading = (async () => {
    if (!document.querySelector('link[data-katex]')) {
      const css = document.createElement("link");
      css.rel = "stylesheet";
      css.href = `${KATEX_BASE}/katex.min.css`;
      css.dataset.katex = "";
      document.head.appendChild(css);
    }
    await loadScript(`${KATEX_BASE}/katex.min.js`);
    await loadScript(`${KATEX_BASE}/contrib/auto-render.min.js`);
  })();
  return katexLoading;
}

/**
 * Typeset every formula inside `root`. Resolves once done, or immediately
 * when the element holds no math. Failures degrade to the source text.
 */
export async function mountMath(root) {
  if (!root || !hasMath(root.textContent)) return;
  try {
    await loadKaTeX();
    window.renderMathInElement(root, {
      delimiters: MATH_DELIMITERS,
      throwOnError: false,
      ignoredTags: ["script", "noscript", "style", "textarea", "pre", "code", "option"],
    });
  } catch (error) {
    console.warn("Math rendering unavailable:", error);
  }
}
