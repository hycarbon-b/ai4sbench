/* ============================================================
   AI4S-Benchmark · Task detail page
   Renders one proposal-board item via ?id=<task_slug|id>.
   Missing fields render gracefully — early proposals are sparse.
   ============================================================ */

import { controlPlaneFetch, currentUser } from "../app.js?v=20260908-review";
import { statusBadge, chip, esc, emptyState, ICONS, formatDate } from "../components.js?v=20260908-review";
import { getTask, invalidateTasks, ROOT } from "../data.js?v=20260908-review";
import { reviewDraft, reviewPayload } from "../review.js?v=20260908-review";

const params = new URLSearchParams(location.search);
const key = params.get("id");

const els = {
  badges: document.getElementById("td-badges"),
  title: document.getElementById("td-title"),
  meta: document.getElementById("td-meta"),
  actions: document.getElementById("td-actions"),
  main: document.getElementById("td-main"),
  aside: document.getElementById("td-aside"),
};

function notFound() {
  document.getElementById("task-detail-root").innerHTML = `
    <div class="container" style="padding-block: var(--space-8);">
      ${emptyState({
        title: "Task not found",
        text: "This task ID does not exist in the current benchmark data. It may have been renamed or not yet published.",
        actionsHTML: `<a class="btn btn--primary" href="${ROOT}tasks/">Browse all tasks</a>`,
      })}
    </div>`;
}

function section(title, bodyHTML, id = "") {
  if (!bodyHTML) return "";
  return `<section${id ? ` id="${id}"` : ""} aria-labelledby="${id || slugify(title)}-h">
    <h2 id="${id || slugify(title)}-h">${esc(title)}</h2>
    ${bodyHTML}
  </section>`;
}

function slugify(s) {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, "-");
}

function para(text) {
  return text ? `<p class="text-secondary">${esc(text)}</p>` : "";
}

function pendingLine(text) {
  return `<p class="text-muted" style="font-size: var(--text-sm);">${esc(text)}</p>`;
}

function listOrDash(items) {
  if (!items || items.length === 0) return null;
  return `<ul>${items.map((i) => `<li class="text-secondary">${esc(i)}</li>`).join("")}</ul>`;
}

function fieldLabel(label, optional = false) {
  return `${esc(label)}${optional ? ' <span class="optional">Optional</span>' : ""}`;
}

function reviewInput(name, label, value, { optional = false, min = null, max = null, hint = "", readonly = false } = {}) {
  return `<div class="form-field">
    <label for="${name}">${fieldLabel(label, optional)}</label>
    <input id="${name}" name="${name}" type="text" value="${esc(value)}"
      ${optional ? "" : "required"}${min ? ` minlength="${min}"` : ""}${max ? ` maxlength="${max}"` : ""}${readonly ? " readonly" : ""}>
    ${hint ? `<p class="hint">${esc(hint)}</p>` : ""}
  </div>`;
}

function reviewTextarea(
  name,
  label,
  value,
  { optional = false, min = null, max = null, hint = "", rows = 4 } = {}
) {
  return `<div class="form-field">
    <label for="${name}">${fieldLabel(label, optional)}</label>
    <textarea id="${name}" name="${name}" rows="${rows}"
      ${optional ? "" : "required"}${min ? ` minlength="${min}"` : ""}${max ? ` maxlength="${max}"` : ""}>${esc(value)}</textarea>
    ${hint ? `<p class="hint">${esc(hint)}</p>` : ""}
  </div>`;
}

function reviewWorkbench(task, user) {
  const draft = reviewDraft(task);
  const decisions = [
    ["approved", "Approved", "Ready for implementation"],
    ["changes_requested", "Changes requested", "Author revision needed"],
    ["rejected", "Rejected", "Not suitable for the benchmark"],
  ];
  return `<section id="review-workbench" class="review-workbench" aria-labelledby="review-workbench-h">
    <div class="review-workbench__head">
      <div>
        <span class="eyebrow">Reviewer workspace</span>
        <h2 id="review-workbench-h">Publish scientific review</h2>
        <p>Signed in as <strong>@${esc(user.github_login)}</strong>. Publishing adds a structured reply to the proposal Discussion.</p>
      </div>
      <span class="review-workbench__current">${task.review_input_valid ? "Current review loaded" : "First review"}</span>
    </div>
    <form id="proposal-review-form" class="review-workbench__form" novalidate>
      ${reviewInput("review_schema_version", "Schema version", draft.review_schema_version, { readonly: true })}

      <fieldset class="review-decision">
        <legend>Decision</legend>
        <div class="review-decision__rail">
          ${decisions
            .map(
              ([value, label, note]) => `<label class="review-decision__option review-decision__option--${value}">
                <input type="radio" name="review_decision" value="${value}" ${draft.review_decision === value ? "checked" : ""} required>
                <span><strong>${label}</strong><small>${note}</small></span>
              </label>`
            )
            .join("")}
        </div>
      </fieldset>

      <div class="review-workbench__group">
        <div class="review-workbench__group-title"><span>01</span><div><h3>Review framing</h3><p>Give readers a compact description, classification and difficulty.</p></div></div>
        ${reviewTextarea("review_short_description", "Short description", draft.review_short_description, { min: 20, max: 2000, rows: 3 })}
        <div class="field-row">
          ${reviewTextarea("review_tags", "Tags", draft.review_tags, { hint: "One tag per line. At least one is required.", rows: 4 })}
          ${reviewInput("review_difficulty", "Difficulty", draft.review_difficulty, { min: 2, max: 80, hint: "For example: Moderate, Hard, or Expert." })}
        </div>
      </div>

      <div class="review-workbench__group">
        <div class="review-workbench__group-title"><span>02</span><div><h3>Scientific assessment</h3><p>Define why the task matters and how success will be measured.</p></div></div>
        ${reviewTextarea("review_scientific_value", "Scientific value", draft.review_scientific_value, { min: 20, max: 8000, rows: 5 })}
        ${reviewTextarea("review_primary_metric", "Primary metric", draft.review_primary_metric, { min: 2, max: 1000, rows: 3 })}
        <div class="field-row">
          ${reviewInput("review_primary_metric_short", "Primary metric short", draft.review_primary_metric_short, { optional: true, max: 240 })}
          ${reviewTextarea("review_secondary_metrics", "Secondary metrics", draft.review_secondary_metrics, { optional: true, hint: "One metric per line.", rows: 3 })}
        </div>
        ${reviewTextarea("review_verification_method", "Verification method", draft.review_verification_method, { min: 20, max: 8000, rows: 5 })}
      </div>

      <div class="review-workbench__group">
        <div class="review-workbench__group-title"><span>03</span><div><h3>Operational detail</h3><p>Optional estimates and evidence make implementation easier to scope.</p></div></div>
        <div class="field-row">
          ${reviewInput("review_estimated_runtime", "Estimated runtime", draft.review_estimated_runtime, { optional: true, max: 240 })}
          ${reviewInput("review_compute_budget", "Compute budget", draft.review_compute_budget, { optional: true, max: 240 })}
        </div>
        ${reviewInput("review_token_budget", "Token budget", draft.review_token_budget, { optional: true, max: 240 })}
        <div class="field-row">
          ${reviewTextarea("review_baseline_results", "Baseline results", draft.review_baseline_results, { optional: true, hint: "One result per line.", rows: 4 })}
          ${reviewTextarea("review_failure_modes", "Failure modes", draft.review_failure_modes, { optional: true, hint: "One failure mode per line.", rows: 4 })}
        </div>
        ${reviewTextarea("review_notes", "Review notes", draft.review_notes, { optional: true, max: 8000, rows: 4 })}
      </div>

      <div class="review-workbench__actions">
        <div>
          <p id="proposal-review-status" class="submit-status" role="status" aria-live="polite"></p>
        </div>
        <div class="submit-actions">
          <button type="button" class="btn btn--secondary" id="proposal-review-preview">Preview reply</button>
          <button type="submit" class="btn btn--primary" id="proposal-review-publish">Publish review</button>
        </div>
      </div>
      <details class="preview-details" id="proposal-review-preview-details" hidden>
        <summary>Discussion reply preview</summary>
        <pre class="proposal-preview" id="proposal-review-preview-body"></pre>
      </details>
    </form>
  </section>`;
}

function wireReviewWorkbench(task, notice = null) {
  const form = document.getElementById("proposal-review-form");
  if (!form) return;
  const statusEl = document.getElementById("proposal-review-status");
  const previewButton = document.getElementById("proposal-review-preview");
  const publishButton = document.getElementById("proposal-review-publish");
  const previewDetails = document.getElementById("proposal-review-preview-details");
  const previewBody = document.getElementById("proposal-review-preview-body");

  const setStatus = (message, state = "") => {
    statusEl.textContent = message;
    statusEl.className = `submit-status${state ? ` is-${state}` : ""}`;
  };
  const setBusy = (busy) => {
    previewButton.disabled = busy;
    publishButton.disabled = busy;
  };
  const validPayload = () => {
    if (!form.checkValidity()) {
      form.reportValidity();
      setStatus("Complete the required review fields.", "error");
      return null;
    }
    return reviewPayload(form);
  };
  const showPreview = (body) => {
    previewBody.textContent = body;
    previewDetails.hidden = false;
    previewDetails.open = true;
  };

  if (notice) {
    setStatus(notice.message, "success");
    if (notice.body) showPreview(notice.body);
  }

  previewButton.addEventListener("click", async () => {
    const payload = validPayload();
    if (!payload) return;
    setBusy(true);
    setStatus("Rendering the Discussion reply…");
    try {
      const result = await controlPlaneFetch("/api/v1/proposals/reviews/preview", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      showPreview(result.comment.body);
      setStatus("Preview ready.", "success");
    } catch (error) {
      setStatus(error.message || "The review preview could not be rendered.", "error");
    } finally {
      setBusy(false);
    }
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = validPayload();
    if (!payload) return;
    setBusy(true);
    setStatus("Publishing the review…");
    try {
      const result = await controlPlaneFetch(`/api/v1/proposals/${encodeURIComponent(task.id)}/reviews`, {
        method: "POST",
        body: JSON.stringify(payload),
      });
      invalidateTasks();
      await render({
        message: "Review published. The current proposal view now reflects this decision.",
        body: result.comment.body,
      });
    } catch (error) {
      setStatus(error.message || "The review could not be published.", "error");
      setBusy(false);
    }
  });
}

async function render(reviewNotice = null) {
  const [task, user] = await Promise.all([
    key ? getTask(key) : null,
    currentUser().catch(() => null),
  ]);
  if (!task) return notFound();
  const identifier = task.discussion_number ? `Proposal #${task.discussion_number}` : task.task_slug;
  document.title = `${identifier} · ${task.title} | AI4S-Benchmark`;
  const desc = document.querySelector('meta[name="description"]');
  if (desc) desc.setAttribute("content", task.review_short_description ?? task.problem);

  /* ---- Hero ---- */
  els.badges.innerHTML = `
    <span class="task-hero__id">${esc(identifier)}</span>
    ${statusBadge(task.status)}`;
  els.title.textContent = task.title;

  const metaBits = [
    `<span><span class="mono-label">Domain</span> &nbsp;<strong style="color:var(--navy);">${esc(task.domain)}</strong></span>`,
    `<span><span class="mono-label">Field</span> &nbsp;${esc(task.field_name)}</span>`,
    `<span><span class="mono-label">Release</span> &nbsp;<span class="mono">${task.revision_release ? esc(task.revision_release) : "—"}</span></span>`,
    `<span><span class="mono-label">Updated</span> &nbsp;<span class="mono">${esc(formatDate(task.updated_at))}</span></span>`,
  ];
  els.meta.innerHTML = metaBits.filter(Boolean).join("");

  const actions = [];
  if (task.discussion_url) {
    actions.push(
      `<a class="btn btn--primary" href="${esc(task.discussion_url)}" target="_blank" rel="noopener">Open proposal Discussion <svg class="ext-arrow" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M4.75 11.25 11.25 4.75M5.9 4.75h5.35v5.35"/></svg></a>`
    );
  }
  if (task.revision_repo_url && task.revision_task_path) {
    actions.push(
      `<a class="btn btn--secondary" href="${esc(task.revision_repo_url)}/tree/${esc(task.revision_commit_sha)}/${esc(task.revision_task_path)}" target="_blank" rel="noopener">Open task revision <svg class="ext-arrow" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M4.75 11.25 11.25 4.75M5.9 4.75h5.35v5.35"/></svg></a>`
    );
  }
  if (task.revision_pull_request_url) {
    actions.push(
      `<a class="btn btn--secondary" href="${esc(task.revision_pull_request_url)}" target="_blank" rel="noopener">Open task PR <svg class="ext-arrow" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M4.75 11.25 11.25 4.75M5.9 4.75h5.35v5.35"/></svg></a>`
    );
  }
  els.actions.innerHTML = actions.join("");

  /* ---- Main column ---- */
  const envRows = [
    `<div><dt>Software and tools</dt><dd>${esc(task.software)}</dd></div>`,
    `<div><dt>Dataset and artifacts</dt><dd>${esc(task.dataset)}</dd></div>`,
    `<div><dt>Requested compute</dt><dd>${esc(task.compute)}</dd></div>`,
  ].filter(Boolean);
  const envHTML = `<dl class="def-grid" style="grid-template-columns: 1fr;">${envRows.join("")}</dl>`;

  const reviewRows = [
    task.review_difficulty ? `<div><dt>Difficulty</dt><dd>${esc(task.review_difficulty)}</dd></div>` : "",
    task.review_primary_metric ? `<div><dt>Primary metric</dt><dd>${esc(task.review_primary_metric)}</dd></div>` : "",
    task.review_secondary_metrics?.length
      ? `<div><dt>Secondary metrics</dt><dd>${task.review_secondary_metrics.map(esc).join(", ")}</dd></div>`
      : "",
    task.review_estimated_runtime ? `<div><dt>Estimated runtime</dt><dd>${esc(task.review_estimated_runtime)}</dd></div>` : "",
    task.review_compute_budget ? `<div><dt>Compute budget</dt><dd>${esc(task.review_compute_budget)}</dd></div>` : "",
    task.review_token_budget ? `<div><dt>Token budget</dt><dd>${esc(task.review_token_budget)}</dd></div>` : "",
  ].filter(Boolean);
  const reviewHTML = task.review_input_valid
    ? `${para(task.review_short_description)}
       ${para(task.review_scientific_value)}
       ${reviewRows.length ? `<dl class="def-grid" style="grid-template-columns: 1fr;">${reviewRows.join("")}</dl>` : ""}
       <div class="verify-panel" style="margin-top: var(--space-4);">
         <div class="verify-panel__title">${ICONS.shield} Verification</div>
         <p style="margin:0; color: var(--ink-secondary);">${esc(task.review_verification_method)}</p>
       </div>
       ${task.review_notes ? para(task.review_notes) : ""}`
    : pendingLine("No valid structured review has been synchronized yet.");

  const resultsHTML = task.revision_agent_results?.length
    ? `<div class="table-wrap"><table class="data-table">
        <thead><tr><th scope="col">Agent</th><th scope="col">Model</th><th scope="col">State</th><th scope="col">Date</th></tr></thead>
        <tbody>${task.revision_agent_results
          .map(
            (r) => `<tr>
            <td><strong>${esc(r.agent)}</strong></td><td class="mono">${esc(r.model)}</td>
            <td>${esc(r.state)}</td><td class="mono">${esc(formatDate(r.created_at))}</td></tr>`
          )
          .join("")}</tbody></table></div>`
    : emptyState({
        title: "No agent evaluations yet",
        text: task.revision_id
          ? "Agent results will appear here once official evaluations run."
          : "Evaluation begins after an approved proposal is linked to a repository revision.",
      });

  const revisionHTML = task.revision_id
    ? `<dl class="def-grid" style="grid-template-columns: 1fr;">
        <div><dt>Commit</dt><dd class="mono">${esc(task.revision_commit_sha)}</dd></div>
        <div><dt>Repository path</dt><dd class="mono">${esc(task.revision_task_path)}</dd></div>
        <div><dt>Resource requirements</dt><dd class="mono">${esc(JSON.stringify(task.revision_resource_requirements ?? {}))}</dd></div>
      </dl>`
    : pendingLine("No repository revision is linked to this proposal yet.");

  els.main.innerHTML = [
    section("Scientific problem", para(task.problem)),
    section("Solvability", para(task.solvability)),
    section("References & resources", para(task.references)),
    section("Requested environment", envHTML),
    section("Expected workflow & outputs", para(task.workflow)),
    section("Proposed evaluation", para(task.evaluation) + `<div class="notice" style="margin-top: var(--space-4);">${ICONS.info}<p><strong>Leakage risk.</strong> ${esc(task.leakage)}</p></div>`, "evaluation"),
    section("Scientific review", reviewHTML, "review"),
    task.review_baseline_results?.length ? section("Baseline results", listOrDash(task.review_baseline_results)) : "",
    task.review_failure_modes?.length ? section("Failure analysis", listOrDash(task.review_failure_modes)) : "",
    section("Repository revision", revisionHTML, "revision"),
    section("Agent results", resultsHTML, "results"),
    user?.can_review ? reviewWorkbench(task, user) : "",
  ].join("");

  /* ---- Aside ---- */
  const glance = [
    ["Status", statusBadge(task.status)],
    ["Difficulty", task.review_difficulty ? esc(task.review_difficulty) : '<span class="text-muted">Pending review</span>'],
    ["Release", `<span class="mono">${task.revision_release ? esc(task.revision_release) : "—"}</span>`],
    ["Created", `<span class="mono">${esc(formatDate(task.created_at))}</span>`],
    ["Updated", `<span class="mono">${esc(formatDate(task.updated_at))}</span>`],
  ];

  const authorHTML = `<div class="person"><span class="person__name">${esc(task.name)}</span>${task.affiliation ? `<span class="person__affil">${esc(task.affiliation)}</span>` : ""}<span class="person__affil">@${esc(task.github)}</span></div>`;
  const reviewerHTML = task.review_reviewer_login
    ? `<div class="person"><span class="person__name">@${esc(task.review_reviewer_login)}</span>${task.review_comment_url ? `<a class="person__affil" href="${esc(task.review_comment_url)}" target="_blank" rel="noopener">Open review reply</a>` : ""}</div>`
    : `<p class="text-muted" style="font-size: var(--text-sm); margin:0;">Reviewer assignment pending.</p>`;

  els.aside.innerHTML = `
    <div class="aside-card">
      <h3>At a glance</h3>
      <ul>${glance.map(([k, v]) => `<li><span class="mono-label">${esc(k)}</span><span>${v}</span></li>`).join("")}</ul>
    </div>
    <div class="aside-card">
      <h3>Task contributor</h3>
      ${authorHTML}
    </div>
    <div class="aside-card">
      <h3>Scientific reviewers</h3>
      ${reviewerHTML}
    </div>
    ${
      (task.review_tags ?? []).length
        ? `<div class="aside-card"><h3>Review tags</h3><div style="display:flex;flex-wrap:wrap;gap:0.4rem;">${task.review_tags.map((tag) => chip(tag)).join("")}</div></div>`
        : ""
    }`;
  wireReviewWorkbench(task, reviewNotice);
}

render().catch((err) => {
  console.error("Task detail failed:", err);
  notFound();
});

document.addEventListener("ai4sbench:authchange", () => {
  void render();
});
