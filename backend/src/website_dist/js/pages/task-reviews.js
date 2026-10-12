/* ============================================================
   AI4S-Benchmark · Task page reviews
   - The Reviews tab: every reviewer's latest review, with a summary
     (decision tally, mean scores) — read from the Discussion.
   - The aside summary card, so reviews are visible without scrolling.
   - The review drawer: reviewers write next to the proposal, not at
     the bottom of it. Each reviewer starts from a blank form (or their
     own saved draft / own previous review) — never someone else's.
   ============================================================ */

import { controlPlaneFetch } from "../app.js?v=20261012-2";
import { esc, formatDate, ICONS } from "../components.js?v=20261012-2";
import { richBlock, mountMath } from "../richtext.js?v=20261012-2";
import {
  CRITERIA,
  CONFIDENCE,
  DECISIONS,
  DECISION_LABEL,
  EXPERT_TIME,
  EXPERT_TIME_LABEL,
  LIMITS,
  MIN_GENERAL,
  RUNTIME_VERDICTS,
  aggregate,
  emptyReview,
  latestPerReviewer,
  normalizeReview,
  parseReviewComment,
  qualityScore,
  toV1Payload,
  validateReview,
} from "../reviews.js?v=20261012-2";
import { loadDiscussionReviews, rememberPublished } from "../discussion.js?v=20261012-2";

const EXT =
  '<svg class="ext-arrow" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M4.75 11.25 11.25 4.75M5.9 4.75h5.35v5.35"/></svg><span class="visually-hidden"> (opens in a new tab)</span>';
const CLOSE =
  '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="m4 4 8 8m0-8-8 8"/></svg>';
const PEN =
  '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10.8 2.7l2.5 2.5L6 12.5l-3.2.7.7-3.2z"/></svg>';

const RUNTIME_LABEL = Object.fromEntries(RUNTIME_VERDICTS.map((v) => [v.value, v.label]));

/* ---- Loading ------------------------------------------------ */

/* The control plane's latest review, rebuilt as a reply body so the
   same parser reads it. Used when GitHub cannot be reached. */
function latestFromTask(task) {
  if (!task.review_decision || !task.review_reviewer_login) return [];
  const sec = (h, v) => (v ? `### ${h}\n\n${Array.isArray(v) ? v.map((x) => `- ${x}`).join("\n") : v}\n` : "");
  const body = [
    "<!-- ai4sbench-proposal-review:v1 -->",
    `Decision: ${task.review_decision}`,
    sec("Short Description", task.review_short_description),
    sec("Difficulty", task.review_difficulty),
    sec("Scientific Value", task.review_scientific_value),
    sec("Primary Metric", task.review_primary_metric),
    sec("Verification Method", task.review_verification_method),
    sec("Estimated Runtime", task.review_estimated_runtime),
    sec("Review Notes", task.review_notes),
  ].join("\n");
  const review = parseReviewComment(body);
  return review
    ? [{
        login: task.review_reviewer_login,
        avatar: "",
        url: task.review_comment_url || task.discussion_url,
        created_at: task.review_created_at,
        updated_at: task.review_updated_at,
        review,
      }]
    : [];
}

const loaded = new Map(); // task.id → { status, items, otherReplies }

export async function loadReviews(task, { fresh = false } = {}) {
  if (!fresh && loaded.has(task.id)) return loaded.get(task.id);
  let state;
  try {
    const { reviews, otherReplies } = await loadDiscussionReviews(task.discussion_url, { fresh });
    state = { status: "ready", items: reviews, otherReplies };
  } catch (error) {
    const reason = error.status === 403 || error.status === 429 ? "GitHub's hourly limit for your network was reached" : "GitHub could not be reached";
    state = { status: "fallback", items: latestFromTask(task), otherReplies: 0, error: reason };
  }
  loaded.set(task.id, state);
  return state;
}

function addLocal(task, item) {
  const state = loaded.get(task.id) ?? { status: "ready", items: [], otherReplies: 0 };
  state.items = [...state.items.filter((i) => i.url !== item.url), item];
  loaded.set(task.id, state);
  rememberPublished(task.discussion_url, item);
}

/* ---- Small pieces ------------------------------------------ */

const avatarURL = (item) =>
  item.avatar ? `${item.avatar}${item.avatar.includes("?") ? "&" : "?"}s=80` : `https://github.com/${encodeURIComponent(item.login)}.png?size=80`;

export function decisionPill(decision) {
  if (!DECISION_LABEL[decision]) return `<span class="decision-pill">No decision</span>`;
  return `<span class="decision-pill decision-pill--${esc(decision)}">${esc(DECISION_LABEL[decision])}</span>`;
}

function dots(n, label) {
  return `<span class="score-dots" role="img" aria-label="${esc(label)}">${[1, 2, 3, 4, 5]
    .map((i) => `<i class="${i <= n ? "is-on" : ""}"></i>`)
    .join("")}</span>`;
}

function tallyBar(tally, count) {
  if (!count) return "";
  return `<span class="tally-bar" aria-hidden="true">${DECISIONS.map((d) =>
    tally[d.value] ? `<i class="tally-bar__${d.value}" style="flex:${tally[d.value]}"></i>` : ""
  ).join("")}</span>`;
}

function tallyText(tally) {
  return DECISIONS.filter((d) => tally[d.value])
    .map((d) => `${tally[d.value]} ${d.past.toLowerCase()}`)
    .join(" · ");
}

/* ---- Review card ------------------------------------------- */

function reviewCard(item, { own = false, current = false } = {}) {
  const r = item.review;
  const scored = CRITERIA.filter((c) => r.scores[c.key]);
  const q = qualityScore(r.scores);
  const edited = item.updated_at && item.created_at && item.updated_at.slice(0, 16) !== item.created_at.slice(0, 16);
  const scoresHTML = scored.length || r.confidence || r.expertTime
    ? `<dl class="rv__scores">
        ${scored
          .map(
            (c) => `<div><dt>${esc(c.label)}</dt><dd>${dots(r.scores[c.key], `${r.scores[c.key]} of 5`)}<span>${esc(c.anchors[r.scores[c.key] - 1])}</span></dd></div>`
          )
          .join("")}
        ${r.expertTime ? `<div><dt>Expert time</dt><dd><span>${esc(EXPERT_TIME_LABEL[r.expertTime])}</span></dd></div>` : ""}
        ${r.confidence ? `<div class="rv__confidence"><dt>Confidence</dt><dd>${dots(r.confidence, `${r.confidence} of 5`)}<span>${esc(CONFIDENCE.anchors[r.confidence - 1])}</span></dd></div>` : ""}
      </dl>`
    : "";
  const legacyFacts = r.format === "v1" && r.legacyDifficulty
    ? `<p class="rv__legacy"><span class="mono-label">Difficulty</span> ${esc(r.legacyDifficulty)}</p>`
    : "";
  const env = r.runtime || r.environment
    ? `<div class="rv__part"><h4>Environment and resources</h4>
        ${r.runtime ? `<p class="rv__runtime"><span class="mono-label">Runtime estimate</span> <span class="runtime-pill runtime-pill--${esc(r.runtime)}">${esc(RUNTIME_LABEL[r.runtime])}</span></p>` : ""}
        ${r.environment ? richBlock(r.environment) : ""}
      </div>`
    : "";
  const evaluation = r.evaluation ? `<div class="rv__part"><h4>Evaluation and leakage</h4>${richBlock(r.evaluation)}</div>` : "";
  const history = item.history?.length
    ? `<details class="rv__history"><summary>${item.history.length} earlier ${item.history.length === 1 ? "review" : "reviews"} by @${esc(item.login)}</summary>
        <ul>${item.history
          .map((h) => `<li>${decisionPill(h.review.decision)} <span class="mono">${esc(formatDate(h.created_at))}</span> <a href="${esc(h.url)}" target="_blank" rel="noopener">Read on GitHub ${EXT}</a></li>`)
          .join("")}</ul>
      </details>`
    : "";

  return `<article class="rv${own ? " rv--own" : ""}" id="review-${esc(String(item.url).split("-").pop())}">
    <header class="rv__head">
      <img class="rv__avatar" src="${esc(avatarURL(item))}" alt="" width="40" height="40" loading="lazy" onerror="this.style.visibility='hidden'">
      <div class="rv__who">
        <a class="rv__login" href="https://github.com/${esc(item.login)}" target="_blank" rel="noopener">@${esc(item.login)}</a>
        <span class="rv__when">${esc(formatDate(item.created_at))}${edited ? " · edited" : ""}${own ? ' · <strong>Your review</strong>' : ""}${current ? ' · <span title="The proposal status on the board follows the most recent review.">sets current status</span>' : ""}</span>
      </div>
      <div class="rv__verdict">${decisionPill(r.decision)}${q ? `<span class="rv__quality" title="Mean of the scored quality criteria">${q.toFixed(1)}<small>/5</small></span>` : ""}</div>
    </header>
    ${scoresHTML}
    ${legacyFacts}
    <div class="rv__part rv__general">${r.format === "v2" ? "<h4>General comments</h4>" : ""}${richBlock(r.general) || '<p class="text-muted">No comments.</p>'}</div>
    ${env}
    ${evaluation}
    <footer class="rv__foot">
      <a href="${esc(item.url)}" target="_blank" rel="noopener">${ICONS.github} Reply on GitHub ${EXT}</a>
      ${history}
    </footer>
  </article>`;
}

/* ---- Panels ------------------------------------------------ */

function summaryHTML(current) {
  const agg = aggregate(current);
  const rows = agg.criteria
    .filter((c) => c.n)
    .map((c) => {
      const label = c.key === "confidence" ? "Reviewer confidence" : CRITERIA.find((x) => x.key === c.key).label;
      return `<li><span class="rv-sum__label">${esc(label)}</span>
        <span class="rv-sum__bar" aria-hidden="true"><i style="width:${(c.mean / 5) * 100}%"></i></span>
        <span class="rv-sum__val mono" title="Mean ${c.mean.toFixed(1)}, lowest ${c.min}, scored by ${c.n}">${c.mean.toFixed(1)}<small> · low ${c.min} · ${c.n}</small></span></li>`;
    })
    .join("");
  return `<div class="rv-sum">
    <div class="rv-sum__head">
      <div class="rv-sum__count"><strong>${agg.count}</strong><span>${agg.count === 1 ? "review" : "reviews"}</span></div>
      <div class="rv-sum__tally">
        ${tallyBar(agg.tally, agg.count)}
        <p>${esc(tallyText(agg.tally))}</p>
      </div>
      ${agg.quality ? `<div class="rv-sum__quality"><strong>${agg.quality.toFixed(1)}</strong><span>mean quality / 5</span></div>` : ""}
    </div>
    ${rows ? `<ul class="rv-sum__rows">${rows}</ul><p class="rv-sum__note">Mean · lowest score · number of reviewers who scored it. Difficulty is a calibration, not a quality score.</p>` : ""}
  </div>`;
}

/** The Reviews tab body. */
export function reviewsPanelHTML(task, state, user) {
  if (!state) return `<p class="text-muted rv-loading">Loading reviews from the Discussion…</p>`;
  const current = latestPerReviewer(state.items);
  const me = String(user?.github_login || "").toLowerCase();
  const notice =
    state.status === "fallback"
      ? `<div class="notice rv-notice">${ICONS.info}<div>Showing the latest review only: ${esc(state.error || "GitHub could not be reached")}. <button type="button" class="link-button" data-reviews-retry>Try again</button></div></div>`
      : "";
  const write = user?.can_review
    ? `<button type="button" class="btn btn--primary btn--sm" data-review-open>${PEN}${current.some((i) => i.login.toLowerCase() === me) ? "Update your review" : "Write a review"}</button>`
    : "";
  const head = `<div class="rv-panel__head">
      <div><h2 id="reviews-h">Scientific reviews</h2>
        <p>Independent reviews by domain reviewers. Each one is a reply on the <a href="${esc(task.discussion_url || "#")}" target="_blank" rel="noopener">proposal Discussion ${EXT}</a>; a reviewer's newest review replaces their earlier ones here.</p>
      </div>${write}</div>`;
  if (!current.length) {
    return `${head}${notice}<div class="rv-empty">
      <p><strong>No reviews yet.</strong> ${user?.can_review ? "Be the first reviewer — it takes a few minutes, and only a recommendation and general comments are required." : "A domain reviewer will assess this proposal. Reviews appear here as soon as they are published."}</p>
    </div>`;
  }
  const currentUrl = task.review_comment_url;
  return `${head}${notice}${summaryHTML(current)}
    <div class="rv-list">${current
      .map((item) => reviewCard(item, { own: item.login.toLowerCase() === me, current: current.length > 1 && item.url === currentUrl }))
      .join("")}</div>
    ${state.otherReplies ? `<p class="rv-more">${state.otherReplies} other ${state.otherReplies === 1 ? "reply" : "replies"} on the <a href="${esc(task.discussion_url)}" target="_blank" rel="noopener">Discussion ${EXT}</a>.</p>` : ""}`;
}

/** Compact aside card: count, tally, who reviewed. */
export function reviewsAsideHTML(state, user) {
  const body = !state
    ? `<p class="text-muted" style="font-size: var(--text-sm); margin:0;">Loading…</p>`
    : (() => {
        const current = latestPerReviewer(state.items);
        if (!current.length) return `<p class="text-muted" style="font-size: var(--text-sm); margin:0;">No reviews yet.</p>`;
        const agg = aggregate(current);
        return `${tallyBar(agg.tally, agg.count)}
          <ul class="rv-aside__list">${current
            .map(
              (i) => `<li><a href="#review-${esc(String(i.url).split("-").pop())}" data-review-jump><img src="${esc(avatarURL(i))}" alt="" width="22" height="22" loading="lazy" onerror="this.style.visibility='hidden'"><span>@${esc(i.login)}</span></a>${decisionPill(i.review.decision)}</li>`
            )
            .join("")}</ul>`;
      })();
  const count = state ? latestPerReviewer(state.items).length : 0;
  return `<div class="aside-card rv-aside" id="td-reviews-card">
    <h3>Reviews${count ? ` · ${count}` : ""}</h3>
    ${body}
    <div class="rv-aside__actions">
      <a class="btn btn--secondary btn--sm" href="#reviews" data-tab-link="reviews">Read reviews</a>
      ${user?.can_review ? `<button type="button" class="btn btn--primary btn--sm" data-review-open>${PEN}Review</button>` : ""}
    </div>
  </div>`;
}

export function ownLatest(state, user) {
  const me = String(user?.github_login || "").toLowerCase();
  if (!me || !state) return null;
  return latestPerReviewer(state.items).find((i) => i.login.toLowerCase() === me) ?? null;
}

/* ---- Drafts (per reviewer, per proposal, this browser only) ---- */

const draftKey = (task, user) => `ai4sbench:review-draft:${String(user.github_login).toLowerCase()}:${task.id}`;
function readDraft(task, user) {
  try {
    const raw = JSON.parse(localStorage.getItem(draftKey(task, user)) || "null");
    return raw ? { review: normalizeReview(raw.review), at: raw.at } : null;
  } catch {
    return null;
  }
}
function writeDraft(task, user, review) {
  try {
    localStorage.setItem(draftKey(task, user), JSON.stringify({ review, at: new Date().toISOString() }));
    return true;
  } catch {
    return false;
  }
}
function clearDraft(task, user) {
  try {
    localStorage.removeItem(draftKey(task, user));
  } catch {
    /* ignore */
  }
}
const isBlank = (r) =>
  !r.decision && !Object.keys(r.scores).length && !r.confidence && !r.runtime && !r.expertTime && !r.general && !r.environment && !r.evaluation;

/* ---- Drawer ------------------------------------------------ */

function scaleHTML(key, c, value) {
  const name = key === "confidence" ? "confidence" : `score_${key}`;
  return `<div class="score" role="radiogroup" aria-labelledby="${name}-l" data-score="${key}">
    <div class="score__head">
      <span class="score__label" id="${name}-l">${esc(c.label)}</span>
      <span class="score__anchor" data-anchor>${value ? `${value} · ${esc(c.anchors[value - 1])}` : "Not scored"}</span>
    </div>
    <p class="score__q">${esc(c.question)}</p>
    <div class="score__scale">
      ${[1, 2, 3, 4, 5]
        .map(
          (n) => `<label title="${n} · ${esc(c.anchors[n - 1])}"><input type="radio" name="${name}" value="${n}"${value === n ? " checked" : ""}><span>${n}</span></label>`
        )
        .join("")}
    </div>
    <div class="score__ends" aria-hidden="true"><span>${esc(c.anchors[0])}</span><span>${esc(c.anchors[4])}</span></div>
  </div>`;
}

function formHTML(task, user, review, { previous, draftAt }) {
  const banner = draftAt
    ? `<div class="rd-banner">Restored your unsaved draft from ${esc(formatDate(draftAt))}. <button type="button" class="link-button" data-rd-reset>Start over</button></div>`
    : previous
      ? `<div class="rd-banner">You reviewed this proposal on ${esc(formatDate(previous.created_at))}. Publishing again adds an updated review that replaces yours here. <button type="button" class="link-button" data-rd-load-previous>Start from my previous review</button></div>`
      : "";
  return `<form class="rd__form" id="rd-form" novalidate>
    <p class="rd__intro">Assess the proposal as the author wrote it — the metric, verification and runtime estimate are theirs to define, and yours to judge. Only the <strong>recommendation</strong> and <strong>general comments</strong> are required.</p>
    ${banner}

    <fieldset class="rd-group">
      <legend>Recommendation <span class="req">Required</span></legend>
      <div class="rd-decisions">
        ${DECISIONS.map(
          (d) => `<label class="rd-decision rd-decision--${d.value}">
            <input type="radio" name="decision" value="${d.value}"${review.decision === d.value ? " checked" : ""}>
            <span><strong>${esc(d.label)}</strong><small>${esc(d.note)}</small></span>
          </label>`
        ).join("")}
      </div>
      <p class="rd-error" data-error="decision" hidden></p>
    </fieldset>

    <div class="rd-group">
      <label class="rd-label" for="rd-general">General comments <span class="req">Required</span></label>
      <p class="rd-hint">Strengths, weaknesses and what the author should change. Markdown and LaTeX ($…$) render on the page.</p>
      <textarea id="rd-general" name="general" rows="8" maxlength="${LIMITS.general}">${esc(review.general)}</textarea>
      <div class="rd-meta"><p class="rd-error" data-error="general" hidden></p><span class="rd-count" data-count="general"></span></div>
    </div>

    <fieldset class="rd-group">
      <legend>Scores <span class="opt">Optional</span></legend>
      <p class="rd-hint">1 = poor, 5 = excellent. Skip anything you cannot judge; click a selected score again to clear it.</p>
      <div class="rd-scores">
        ${CRITERIA.map((c) => scaleHTML(c.key, c, review.scores[c.key] ?? null)).join("")}
        <div class="score score--chips">
          <div class="score__head"><span class="score__label" id="rd-time-l">Time an expert would need</span></div>
          <p class="score__q">Roughly how long would a domain expert need to solve this by hand?</p>
          <div class="rd-chips" role="radiogroup" aria-labelledby="rd-time-l">
            ${EXPERT_TIME.map(
              (v) => `<label class="rd-chip"><input type="radio" name="expertTime" value="${v.value}"${review.expertTime === v.value ? " checked" : ""}><span>${esc(v.label)}</span></label>`
            ).join("")}
          </div>
        </div>
        ${scaleHTML("confidence", CONFIDENCE, review.confidence)}
      </div>
    </fieldset>

    <fieldset class="rd-group">
      <legend>Environment and resources <span class="opt">Optional</span></legend>
      <p class="rd-hint">Is the author's runtime and compute estimate reasonable? Are the software and data obtainable?</p>
      <div class="rd-chips" role="radiogroup" aria-label="Runtime estimate">
        ${RUNTIME_VERDICTS.map(
          (v) => `<label class="rd-chip"><input type="radio" name="runtime" value="${v.value}"${review.runtime === v.value ? " checked" : ""}><span>${esc(v.label)}</span></label>`
        ).join("")}
      </div>
      <textarea id="rd-environment" name="environment" rows="4" maxlength="${LIMITS.environment}" aria-label="Comments on the environment">${esc(review.environment)}</textarea>
      <div class="rd-meta"><span></span><span class="rd-count" data-count="environment"></span></div>
    </fieldset>

    <div class="rd-group">
      <label class="rd-label" for="rd-evaluation">Evaluation and leakage <span class="opt">Optional</span></label>
      <p class="rd-hint">Does the proposed evaluation measure the science? Could an agent pass by shortcut, memorisation or look-up?</p>
      <textarea id="rd-evaluation" name="evaluation" rows="4" maxlength="${LIMITS.evaluation}">${esc(review.evaluation)}</textarea>
      <div class="rd-meta"><span></span><span class="rd-count" data-count="evaluation"></span></div>
    </div>
  </form>`;
}

function readForm(form) {
  const data = new FormData(form);
  const scores = {};
  CRITERIA.forEach((c) => {
    const v = Number(data.get(`score_${c.key}`));
    if (v) scores[c.key] = v;
  });
  return normalizeReview({
    decision: data.get("decision"),
    scores,
    confidence: Number(data.get("confidence")) || null,
    runtime: data.get("runtime") || "",
    expertTime: data.get("expertTime") || "",
    general: data.get("general"),
    environment: data.get("environment"),
    evaluation: data.get("evaluation"),
  });
}

function publishError(error) {
  switch (error?.status) {
    case 401:
      return "Your GitHub sign-in expired. Sign in again, then publish — your draft is saved in this browser.";
    case 403:
      return "Your GitHub account is not an approved reviewer yet, so the review was not published. Your draft is saved.";
    case 404:
      return "This proposal no longer exists.";
    case 409:
      return "This proposal has no linked GitHub Discussion, so reviews cannot be posted yet.";
    case 502:
      return "GitHub did not accept the reply. This is usually temporary — try again in a minute. Your draft is saved.";
    default:
      return error?.message || "The review could not be published. Your draft is saved.";
  }
}

let drawerEl = null;
let lastFocus = null;

export function closeReviewDrawer() {
  if (!drawerEl) return;
  drawerEl.classList.remove("is-open");
  document.body.classList.remove("is-reviewing");
  const el = drawerEl;
  drawerEl = null;
  setTimeout(() => el.remove(), 220);
  lastFocus?.focus?.();
}

/**
 * Open the review drawer. `onPublished(item)` runs after the control
 * plane accepts the review; the page repaints from local state, so the
 * reviewer does not wait for another round trip.
 */
export function openReviewDrawer({ task, user, state, onPublished }) {
  if (drawerEl) {
    drawerEl.querySelector("textarea, input")?.focus();
    return;
  }
  lastFocus = document.activeElement;
  const previous = ownLatest(state, user);
  const draft = readDraft(task, user);
  const start = draft && !isBlank(draft.review) ? draft.review : emptyReview();
  const identifier = task.discussion_number ? `Proposal #${task.discussion_number}` : "Proposal";

  drawerEl = document.createElement("aside");
  drawerEl.className = "rd";
  drawerEl.setAttribute("role", "dialog");
  drawerEl.setAttribute("aria-labelledby", "rd-title");
  drawerEl.innerHTML = `
    <header class="rd__head">
      <div>
        <span class="rd__eyebrow">${esc(identifier)} · reviewing as @${esc(user.github_login)}</span>
        <h2 id="rd-title">${previous ? "Update your review" : "Your review"}</h2>
      </div>
      <button type="button" class="rd__close" aria-label="Close the review panel (your draft is kept)">${CLOSE}</button>
    </header>
    <div class="rd__body"></div>
    <footer class="rd__foot">
      <p class="rd__status" role="status" aria-live="polite"></p>
      <div class="rd__buttons">
        <button type="button" class="btn btn--secondary btn--sm" data-rd-discard>Discard draft</button>
        <button type="submit" form="rd-form" class="btn btn--primary" data-rd-publish>Publish review</button>
      </div>
    </footer>`;
  document.body.append(drawerEl);
  requestAnimationFrame(() => {
    drawerEl?.classList.add("is-open");
    document.body.classList.add("is-reviewing");
  });

  const body = drawerEl.querySelector(".rd__body");
  const statusEl = drawerEl.querySelector(".rd__status");
  const publishBtn = drawerEl.querySelector("[data-rd-publish]");
  const discardBtn = drawerEl.querySelector("[data-rd-discard]");
  const setStatus = (msg, kind = "") => {
    statusEl.textContent = msg;
    statusEl.className = `rd__status${kind ? ` is-${kind}` : ""}`;
  };

  let saveTimer = 0;
  const mount = (review, opts) => {
    body.innerHTML = formHTML(task, user, review, opts);
    wire();
  };

  function wire() {
    const form = body.querySelector("#rd-form");
    const counts = () =>
      form.querySelectorAll("[data-count]").forEach((el) => {
        const field = form.elements[el.dataset.count];
        el.textContent = `${field.value.length.toLocaleString()} / ${LIMITS[el.dataset.count].toLocaleString()}`;
      });
    counts();

    // Radios that can be cleared by clicking the selected one again.
    form.querySelectorAll(".score__scale input, .rd-chip input").forEach((input) => {
      input.addEventListener("pointerdown", () => (input.dataset.was = input.checked ? "1" : ""));
      input.addEventListener("click", () => {
        if (input.dataset.was === "1") {
          input.checked = false;
          input.dataset.was = "";
          form.dispatchEvent(new Event("input"));
        }
      });
    });

    form.addEventListener("input", () => {
      counts();
      form.querySelectorAll(".score[data-score]").forEach((el) => {
        const key = el.dataset.score;
        const c = key === "confidence" ? CONFIDENCE : CRITERIA.find((x) => x.key === key);
        const checked = el.querySelector("input:checked");
        el.querySelector("[data-anchor]").textContent = checked ? `${checked.value} · ${c.anchors[checked.value - 1]}` : "Not scored";
        el.classList.toggle("is-scored", Boolean(checked));
      });
      form.querySelectorAll("[data-error]").forEach((e) => (e.hidden = true));
      clearTimeout(saveTimer);
      saveTimer = setTimeout(() => {
        const review = readForm(form);
        if (isBlank(review)) clearDraft(task, user);
        else if (writeDraft(task, user, review)) setStatus("Draft saved in this browser.");
      }, 500);
    });
    form.dispatchEvent(new Event("input"));
    setStatus(draft && !isBlank(draft.review) ? "Draft restored." : "");

    body.querySelector("[data-rd-load-previous]")?.addEventListener("click", () => {
      mount(normalizeReview(previous.review), { previous: null, draftAt: null });
      setStatus("Loaded your previous review. Edit it, then publish.");
    });
    body.querySelector("[data-rd-reset]")?.addEventListener("click", () => {
      clearDraft(task, user);
      mount(emptyReview(), { previous, draftAt: null });
      setStatus("Started a blank review.");
    });

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const review = readForm(form);
      const errors = validateReview(review);
      if (Object.keys(errors).length) {
        for (const [field, msg] of Object.entries(errors)) {
          const el = form.querySelector(`[data-error="${field}"]`);
          if (el) {
            el.textContent = msg;
            el.hidden = false;
          }
        }
        const first = Object.keys(errors)[0];
        (first === "decision" ? form.querySelector('input[name="decision"]') : form.elements[first])?.focus();
        setStatus("Add the required fields to publish.", "error");
        return;
      }
      writeDraft(task, user, review);
      publishBtn.disabled = true;
      discardBtn.disabled = true;
      publishBtn.textContent = "Publishing…";
      setStatus("Posting your review to the GitHub Discussion. This usually takes a few seconds.");
      try {
        const payload = toV1Payload(review, task);
        const result = await controlPlaneFetch(`/api/v1/proposals/${encodeURIComponent(task.id)}/reviews`, {
          method: "POST",
          body: JSON.stringify(payload),
        });
        clearDraft(task, user);
        const now = new Date().toISOString();
        const item = {
          login: user.github_login,
          avatar: "",
          url: result.review_comment_url,
          created_at: now,
          updated_at: now,
          review: parseReviewComment(result.comment?.body) ?? { format: "v2", ...review },
        };
        addLocal(task, item);
        // The control plane updated the proposal row synchronously; mirror it
        // locally instead of refetching the whole board.
        Object.assign(task, payload, {
          status: review.decision,
          review_reviewer_login: user.github_login,
          review_comment_url: result.review_comment_url,
          review_created_at: now,
          review_updated_at: now,
          review_input_valid: true,
        });
        showPublished(item);
        onPublished?.(item);
      } catch (error) {
        setStatus(publishError(error), "error");
        publishBtn.disabled = false;
        discardBtn.disabled = false;
        publishBtn.textContent = "Publish review";
      }
    });
  }

  function showPublished(item) {
    body.innerHTML = `<div class="rd-done">
      <span class="rd-done__icon">${ICONS.check}</span>
      <h3>Review published</h3>
      <p>Your review is now a reply on the proposal Discussion and is listed under <strong>Reviews</strong> on this page.</p>
      <div class="rd-done__actions">
        <a class="btn btn--secondary btn--sm" href="${esc(item.url)}" target="_blank" rel="noopener">View on GitHub ${EXT}</a>
        <button type="button" class="btn btn--primary btn--sm" data-rd-close>Done</button>
      </div>
    </div>`;
    drawerEl.querySelector(".rd__foot").hidden = true;
    body.querySelector("[data-rd-close]").addEventListener("click", closeReviewDrawer);
    body.querySelector("[data-rd-close]").focus();
  }

  drawerEl.querySelector(".rd__close").addEventListener("click", closeReviewDrawer);
  discardBtn.addEventListener("click", () => {
    clearDraft(task, user);
    mount(emptyReview(), { previous, draftAt: null });
    setStatus("Draft discarded.");
  });
  drawerEl.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeReviewDrawer();
  });

  mount(start, { previous, draftAt: draft && !isBlank(draft.review) ? draft.at : null });
  setTimeout(() => drawerEl?.querySelector('input[name="decision"]:checked, input[name="decision"]')?.focus(), 60);
}

export { mountMath };
