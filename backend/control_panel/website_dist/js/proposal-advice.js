/* ============================================================
   AI4S-Benchmark · Proposal checklist UI
   Shows checkProposal() findings (js/proposal-check.js) in two
   forms: a note under each field while the author writes, and a
   full checklist (review step, editor, author's task page).
   Advice only — it never blocks saving or submitting.
   ============================================================ */

import { checkProposal, summarize, FIELD_NAMES } from "./proposal-check.js?v=20260921-3";
import { esc, taskURL } from "./components.js?v=20260921-3";
import { getTasks } from "./data.js?v=20260921-3";

const ICON = {
  warn: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M8 1.8 15 14H1z"/><path d="M8 6.2v3.6M8 11.9v.1"/></svg>',
  tip: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" aria-hidden="true"><path d="M5.6 11.2c-1.3-1-2.1-2.4-2.1-4A4.5 4.5 0 0 1 8 2.7a4.5 4.5 0 0 1 4.5 4.5c0 1.6-.8 3-2.1 4M6 13.2h4M6.6 15h2.8"/></svg>',
  ok: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="8" cy="8" r="6.4"/><path d="m5.3 8.2 1.8 1.9 3.6-3.9"/></svg>',
};

/* The public board, for duplicate-title checks. A failure just skips them. */
let boardPromise = null;
function board() {
  boardPromise ??= getTasks().catch(() => []);
  return boardPromise;
}

function messageHTML(finding) {
  const text = esc(finding.message);
  return finding.taskId ? `${text} <a href="${taskURL({ id: finding.taskId })}" target="_blank" rel="noopener">Open it</a>` : text;
}

/**
 * The checklist as HTML. `goto: true` adds a button per finding that
 * carries data-goto-field="<field>" for the caller to wire up.
 */
export function checklistHTML(findings, { title = "Proposal checklist", intro = "", goto = false, compact = false } = {}) {
  const items = findings.length
    ? findings
        .map(
          (f) => `<li class="is-${f.level}">${ICON[f.level]}<span>${compact ? "" : `<span class="checklist__field">${esc(FIELD_NAMES[f.field] ?? f.field)}</span>`}${messageHTML(f)}${
            goto ? ` <button type="button" class="checklist__goto" data-goto-field="${esc(f.field)}">Go to field</button>` : ""
          }</span></li>`
        )
        .join("")
    : `<li class="is-ok">${ICON.ok}<span>Every check passed: sources, evaluation criteria, compute budget, math formatting and duplicates.</span></li>`;
  return `<div class="checklist" aria-live="polite">
    <div class="checklist__head"><h3>${esc(title)}</h3><span class="checklist__summary">${esc(summarize(findings))}</span></div>
    ${intro ? `<p class="checklist__intro">${intro}</p>` : ""}
    <ul>${items}</ul>
  </div>`;
}

/** Run the checks against the current public board. */
export async function adviseOn(answers, { selfId = null } = {}) {
  return checkProposal(answers, { existing: await board(), selfId });
}

/**
 * Show findings as notes under their fields inside `form` (wrappers are
 * [data-field="<key>"]). Notes appear for a field once the author has
 * left it (or immediately with `showAll`), and update as they type.
 * Returns { refresh, findings, showAll }.
 */
export function mountFieldAdvice(form, answers, { selfId = null, showAll = false } = {}) {
  const touched = new Set();
  let all = showAll;
  let latest = [];
  let timer = null;

  async function refresh() {
    latest = await adviseOn(answers(), { selfId });
    form.querySelectorAll("[data-advice]").forEach((el) => el.remove());
    const byField = new Map();
    for (const f of latest) {
      if (!all && !touched.has(f.field)) continue;
      if (!byField.has(f.field)) byField.set(f.field, []);
      byField.get(f.field).push(f);
    }
    for (const [field, list] of byField) {
      const wrap = form.querySelector(`[data-field="${field}"]`);
      if (!wrap) continue;
      for (const f of list) {
        const note = document.createElement("p");
        note.className = `advice-note${f.level === "tip" ? " advice-note--tip" : ""}`;
        note.dataset.advice = f.id;
        note.innerHTML = `${ICON[f.level]}<span>${messageHTML(f)}</span>`;
        wrap.appendChild(note);
      }
    }
    return latest;
  }

  const schedule = () => {
    clearTimeout(timer);
    timer = setTimeout(refresh, 450);
  };
  form.addEventListener("focusout", (e) => {
    const key = e.target?.name;
    if (!key || !(key in FIELD_NAMES)) return;
    touched.add(key);
    schedule();
  });
  form.addEventListener("input", schedule);
  if (all) void refresh();

  return {
    refresh,
    findings: () => latest,
    showAll() {
      all = true;
      return refresh();
    },
  };
}
