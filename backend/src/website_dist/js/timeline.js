/* ============================================================
   AI4S-Benchmark · Lifecycle timeline
   Renders lifecycle(task) (js/lifecycle.js) as the five-stage
   track shown at the top of every task page.
   ============================================================ */

import { esc, formatDate, ICONS } from "./components.js?v=20260921-3";
import { lifecycle } from "./lifecycle.js?v=20260921-3";
import { ROOT } from "./data.js?v=20260921-3";

const EXT =
  '<svg class="ext-arrow" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M4.75 11.25 11.25 4.75M5.9 4.75h5.35v5.35"/></svg><span class="visually-hidden"> (opens in a new tab)</span>';
const CROSS =
  '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="m5 5 6 6m0-6-6 6"/></svg>';

const NOW_LABEL = { current: "Now", attention: "Action needed", stopped: "Ended" };

function linkHTML(link) {
  if (!link) return "";
  if (link.external) {
    return `<a class="stagetrack__link" href="${esc(link.href)}" target="_blank" rel="noopener">${esc(link.label)} ${EXT}</a>`;
  }
  const href = link.internal ? `${ROOT}${link.href}` : link.href;
  return `<a class="stagetrack__link" href="${esc(href)}">${esc(link.label)}</a>`;
}

function stepHTML(step) {
  const dot = step.state === "done" ? ICONS.check : step.state === "stopped" ? CROSS : String(step.index + 1);
  const meta = [step.date ? formatDate(step.date) : "", step.by ? `@${step.by}` : ""].filter(Boolean).join(" · ");
  const now = NOW_LABEL[step.state];
  return `<li class="stagetrack__step is-${step.state}" title="${esc(step.description)}"${now ? ' aria-current="step"' : ""}>
    <span class="stagetrack__dot" aria-hidden="true">${dot}</span>
    <span class="stagetrack__label">${esc(step.label)}${now ? `<span class="stagetrack__now">${now}</span>` : ""}</span>
    <span class="stagetrack__note">${esc(step.note)}</span>
    ${meta ? `<span class="stagetrack__meta">${esc(meta)}</span>` : ""}
    ${linkHTML(step.link)}
  </li>`;
}

/** The timeline section for one task. Safe HTML. */
export function timelineHTML(task) {
  const steps = lifecycle(task);
  const at = steps.find((s) => NOW_LABEL[s.state]) ?? steps[steps.length - 1];
  return `<section class="stagetrack" aria-labelledby="lifecycle-h">
    <div class="stagetrack__head">
      <h2 id="lifecycle-h">Task lifecycle</h2>
      <p>Stage ${at.index + 1} of ${steps.length} · <a href="${ROOT}guide/">How a proposal becomes a task</a></p>
    </div>
    <ol class="stagetrack__track">${steps.map(stepHTML).join("")}</ol>
  </section>`;
}
