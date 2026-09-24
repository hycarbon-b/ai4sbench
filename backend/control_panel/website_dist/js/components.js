/* ============================================================
   AI4S-Benchmark · Shared render helpers
   ============================================================ */

import { ROOT } from "./data.js?v=20260921-3";
import { displayStatus, lifecycle, STATUS_INFO } from "./lifecycle.js?v=20260921-3";
import { excerptHTML } from "./richtext.js?v=20260921-3";

/** Escape untrusted-ish text before inserting into HTML strings. */
export function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

/* ---- Status system ---------------------------------------- */

const STATUS_ICONS = {
  pending:
    '<svg viewBox="0 0 12 12" aria-hidden="true"><circle cx="6" cy="6" r="4.4" fill="none" stroke="currentColor" stroke-width="1.6" stroke-dasharray="2.3 2"/></svg>',
  changes_requested:
    '<svg viewBox="0 0 12 12" aria-hidden="true"><circle cx="6" cy="6" r="4.4" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M6 3.4v2.8l1.9 1.4" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>',
  approved:
    '<svg viewBox="0 0 12 12" aria-hidden="true"><circle cx="6" cy="6" r="4.8" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M3.8 6.1l1.5 1.6 2.9-3.2" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  rejected:
    '<svg viewBox="0 0 12 12" aria-hidden="true"><circle cx="6" cy="6" r="4.8" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="m4.3 4.3 3.4 3.4m0-3.4L4.3 7.7" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
  proposed:
    '<svg viewBox="0 0 12 12" aria-hidden="true"><circle cx="6" cy="6" r="4.4" fill="none" stroke="currentColor" stroke-width="1.6" stroke-dasharray="2.3 2"/></svg>',
  under_review:
    '<svg viewBox="0 0 12 12" aria-hidden="true"><circle cx="6" cy="6" r="4.4" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M6 3.4v2.8l1.9 1.4" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>',
  agent_testing:
    '<svg viewBox="0 0 12 12" aria-hidden="true"><circle cx="6" cy="6" r="4.4" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M5 4.2l2.8 1.8L5 7.8z" fill="currentColor"/></svg>',
  verified:
    '<svg viewBox="0 0 12 12" aria-hidden="true"><circle cx="6" cy="6" r="4.8" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M3.8 6.1l1.5 1.6 2.9-3.2" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  released:
    '<svg viewBox="0 0 12 12" aria-hidden="true"><circle cx="6" cy="6" r="4.6" fill="currentColor" opacity="0.25"/><circle cx="6" cy="6" r="2.4" fill="currentColor"/></svg>',
  implementation:
    '<svg viewBox="0 0 12 12" aria-hidden="true"><circle cx="3.4" cy="3" r="1.4" fill="none" stroke="currentColor" stroke-width="1.3"/><circle cx="3.4" cy="9" r="1.4" fill="none" stroke="currentColor" stroke-width="1.3"/><circle cx="8.6" cy="9" r="1.4" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M3.4 4.4v3.2M8.6 7.6V5.2a1.6 1.6 0 0 0-1.6-1.6H5.6" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>',
};

const STATUS_LABELS = {
  pending: "Pending Review",
  changes_requested: "Changes Requested",
  approved: "Approved",
  rejected: "Rejected",
  proposed: "Proposed",
  under_review: "Under Review",
  agent_testing: "Agent Testing",
  verified: "Verified",
  released: "Released",
  implementation: "Task PR",
};

/* Hover text for every badge: what the status means and what happens next. */
const STATUS_TITLES = {
  proposed: "A researcher has proposed this task. It has not yet entered formal scientific review.",
  under_review: "Domain reviewers are assessing scientific value, verifiability and difficulty.",
  verified: "The task, environment and evaluator have been validated.",
  ...Object.fromEntries(Object.entries(STATUS_INFO).map(([key, info]) => [key, info.description])),
};

export function statusBadge(status) {
  const label = STATUS_LABELS[status] ?? status;
  const icon = STATUS_ICONS[status] ?? "";
  const title = STATUS_TITLES[status];
  return `<span class="badge badge--${esc(status)}"${title ? ` title="${esc(title)}"` : ""}>${icon}${esc(label)}</span>`;
}

/**
 * Five-segment progress meter for a task row: where the task sits in
 * proposal → review → task PR → evaluation → release.
 */
export function stageMeter(task) {
  const stages = lifecycle(task);
  const now = stages.find((s) => ["current", "attention", "stopped"].includes(s.state)) ?? stages[stages.length - 1];
  const summary = `Stage ${now.index + 1} of ${stages.length}: ${now.label} — ${now.note}`;
  return `<span class="stage-meter" title="${esc(summary)}" role="img" aria-label="${esc(summary)}">
    <span class="stage-meter__bar" aria-hidden="true">${stages.map((s) => `<i class="is-${s.state}"></i>`).join("")}</span>
    <span class="stage-meter__label">${now.index + 1}/${stages.length} · ${esc(now.short)}</span>
  </span>`;
}

export function chip(text) {
  return `<span class="chip">${esc(text)}</span>`;
}

/* ---- Icons ------------------------------------------------- */

export const ICONS = {
  discord:
    '<svg viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><path d="M13.05 3.02A12.3 12.3 0 0 0 9.99 2.1a8.5 8.5 0 0 0-.39.79 11.4 11.4 0 0 0-3.2 0 8.4 8.4 0 0 0-.4-.79 12.3 12.3 0 0 0-3.06.92C1 5.9.46 8.7.73 11.47a12.4 12.4 0 0 0 3.76 1.88c.3-.41.57-.85.8-1.31a8 8 0 0 1-1.26-.6c.1-.08.21-.16.31-.24a8.85 8.85 0 0 0 7.52 0l.31.24c-.4.24-.82.44-1.26.6.23.46.5.9.8 1.31a12.4 12.4 0 0 0 3.76-1.88c.32-3.2-.54-5.98-2.42-8.45ZM5.66 9.77c-.73 0-1.33-.67-1.33-1.49s.58-1.5 1.33-1.5 1.35.68 1.34 1.5c0 .82-.59 1.49-1.34 1.49Zm4.68 0c-.73 0-1.33-.67-1.33-1.49s.58-1.5 1.33-1.5 1.35.68 1.34 1.5c0 .82-.59 1.49-1.34 1.49Z"/></svg>',
  github:
    '<svg viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z"/></svg>',
  check:
    '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2.8 8.4l3.2 3.4 7.2-7.6"/></svg>',
  shield:
    '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true"><path d="M8 1.5l5.5 2v4c0 3.4-2.3 5.8-5.5 7-3.2-1.2-5.5-3.6-5.5-7v-4z"/><path d="M5.6 8l1.7 1.8 3.1-3.4" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  info:
    '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true"><circle cx="8" cy="8" r="6.4"/><path d="M8 7.2v3.6M8 5v.1" stroke-linecap="round"/></svg>',
  search:
    '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><circle cx="7" cy="7" r="4.6"/><path d="M10.5 10.5L14 14" stroke-linecap="round"/></svg>',
  arrowRight:
    '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2.5 8h11M9.5 4l4 4-4 4"/></svg>',
  chevron:
    '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 3.5L10.5 8 6 12.5"/></svg>',
};

/* ---- Orbit motif (empty states, accents) ------------------ */

export function orbitSVG(cls = "empty-state__orbit") {
  return `<svg class="${cls}" viewBox="0 0 120 120" fill="none" aria-hidden="true">
    <g class="orbit-anim">
      <path d="M 21.9 82 A 44 44 0 1 1 41.4 99.9" stroke="#7E8AA3" stroke-width="2.4" stroke-linecap="round"/>
      <circle cx="16" cy="52" r="4" fill="#18B7C9"/>
      <circle cx="92" cy="92" r="4" fill="#7E8AA3"/>
    </g>
    <ellipse cx="60" cy="60" rx="29" ry="16" transform="rotate(-30 60 60)" stroke="#7E8AA3" stroke-width="1.4" stroke-dasharray="4 5"/>
    <circle cx="60" cy="60" r="11" fill="#0D1730"/>
    <circle cx="92" cy="27" r="6.5" fill="#2474FF"/>
  </svg>`;
}

export function emptyState({ title, text, actionsHTML = "" }) {
  return `<div class="empty-state">
    ${orbitSVG()}
    <h3>${esc(title)}</h3>
    <p>${esc(text)}</p>
    ${actionsHTML}
  </div>`;
}

/* ---- Formatting ------------------------------------------- */

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/**
 * "2026-09-11T20:02:56.340053" → "11 Sep 2026". Control-plane timestamps
 * carry no zone and are UTC; anything unparseable is returned as-is.
 */
export function formatDate(iso) {
  if (!iso) return "—";
  const text = String(iso);
  const hasZone = /(Z|[+-]\d\d:?\d\d)$/.test(text);
  const date = new Date(text.length === 10 || hasZone ? text : `${text}Z`);
  if (Number.isNaN(date.getTime())) return text;
  return `${date.getUTCDate()} ${MONTHS[date.getUTCMonth()]} ${date.getUTCFullYear()}`;
}

export function taskURL(task) {
  return `${ROOT}tasks/task.html?id=${encodeURIComponent(task.id)}`;
}

export function peopleLine(people, fallback) {
  if (!people || people.length === 0) return fallback;
  return people
    .map((p) => `${esc(p.name)}${p.affiliation ? ` · ${esc(p.affiliation)}` : ""}`)
    .join(", ");
}

/* ---- Task card -------------------------------------------- */

/**
 * One task as a compact list row. The explorer is built to hold hundreds of
 * tasks, so a row carries only what you scan by — identifier, title, a
 * one-line summary, review tags and status. Everything else (metric detail,
 * environment, evaluation) lives on the task page.
 */
export function taskCard(task) {
  const chips = [
    ...(task.field_name ? [chip(task.field_name)] : []),
    ...(task.review_tags ?? []).map((tag) => chip(tag)),
  ].join("");

  const metricLabel = task.review_primary_metric_short ?? task.review_primary_metric;
  const facts = [
    metricLabel ? esc(shortMetric(metricLabel)) : "",
    task.review_difficulty ? esc(task.review_difficulty) : "",
    task.revision_release ? esc(task.revision_release) : "",
  ].filter(Boolean);

  const identifier = task.discussion_number ? `Proposal #${task.discussion_number}` : task.task_slug;

  return `<article class="card--interactive task-row">
    <div class="task-row__head">
      <span class="task-row__id mono">${esc(identifier)}</span>
      <h3 class="task-row__title"><a href="${taskURL(task)}">${esc(task.title)}</a></h3>
      <span class="task-row__badges">${statusBadge(displayStatus(task))}</span>
    </div>
    <p class="task-row__desc">${excerptHTML(task.review_short_description ?? task.problem)}</p>
    <div class="task-row__foot">
      <span class="task-row__chips">${chips}</span>
      ${stageMeter(task)}
      <span class="task-row__facts mono">${facts.join(" · ")}${facts.length ? " · " : ""}${esc(formatDate(task.updated_at))}</span>
    </div>
  </article>`;
}

/** Compress long metric descriptions for card display. */
function shortMetric(metric) {
  if (metric.length <= 34) return metric;
  return metric.slice(0, 32) + "…";
}
