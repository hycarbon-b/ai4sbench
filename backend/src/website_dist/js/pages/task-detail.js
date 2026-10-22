/* ============================================================
   AI4S-Benchmark · Task detail page
   Renders one proposal-board item via ?id=<task_slug|id>.
   Missing fields render gracefully — early proposals are sparse.
   ============================================================ */

import { currentUser } from "../app.js?v=20261015-2";
import { statusBadge, esc, emptyState, ICONS, formatDate } from "../components.js?v=20261022-reviewer1";
import { getSite, getTask, invalidateTasks, ROOT } from "../data.js?v=20261012-2";
import {
  loadReviews,
  reviewsPanelHTML,
  reviewsAsideHTML,
  ownLatest,
  openReviewDrawer,
  closeReviewDrawer,
} from "./task-reviews.js?v=20261015-2";
import { latestPerReviewer } from "../reviews.js?v=20261012-2";
import { richBlock, mountMath } from "../richtext.js?v=20261012-2";
import { splitContributors } from "../people.js?v=20261012-2";
import { canEdit, mountEditor, editingAvailable } from "./task-edit.js?v=20261015-2";
import { displayStatus } from "../lifecycle.js?v=20261012-2";
import { timelineHTML } from "../timeline.js?v=20261012-2";
import { adviseOn, checklistHTML } from "../proposal-advice.js?v=20261012-2";

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

/* Proposal text is written like a Discussion post: paragraphs, lists,
   links, Markdown and LaTeX. Render it as such instead of one flat line. */
function para(text) {
  return richBlock(text);
}

/* ---- Reviews: tabs, aside card, drawer ---- */
const reviewState = new Map(); // task.id → loaded reviews
let ctx = { task: null, user: null };

function tabFromHash() {
  return /^#(reviews|review-)/.test(location.hash) ? "reviews" : "proposal";
}

function selectTab(name, { focus = true, updateHash = false } = {}) {
  document.querySelectorAll(".td-tabs [role=tab]").forEach((tab) => {
    const on = tab.dataset.tab === name;
    tab.setAttribute("aria-selected", String(on));
    tab.tabIndex = on ? 0 : -1;
  });
  document.querySelectorAll(".td-panel").forEach((panel) => {
    panel.hidden = panel.id !== `panel-${name}`;
  });
  if (updateHash) history.replaceState(null, "", name === "reviews" ? "#reviews" : location.pathname + location.search);
  if (focus) document.querySelector(".td-tabs")?.scrollIntoView({ behavior: "smooth", block: "start" });
}

/* Everything that depends on the loaded reviews, repainted in place. */
function paintReviews(state) {
  const { task, user } = ctx;
  if (!task) return;
  const panel = document.getElementById("panel-reviews");
  if (panel && state) {
    panel.innerHTML = reviewsPanelHTML(task, state, user);
    void mountMath(panel);
  }
  document.getElementById("td-reviews-card")?.replaceWith(
    Object.assign(document.createElement("div"), { innerHTML: reviewsAsideHTML(state, user) }).firstElementChild
  );
  const count = state ? latestPerReviewer(state.items).length : 0;
  document.querySelectorAll("[data-review-count]").forEach((el) => (el.textContent = count ? String(count) : ""));
  const mine = ownLatest(state, user);
  document.querySelectorAll("[data-review-label]").forEach((el) => (el.textContent = mine ? "Update your review" : "Write a review"));
  // A deep link to one review (#review-123) lands on it once it exists.
  if (state && /^#review-\d+$/.test(location.hash)) document.querySelector(location.hash)?.scrollIntoView({ block: "start" });
}

/* After a publish: the drawer already patched the task; repaint the bits
   that show its status instead of reloading the whole page. */
function afterPublished() {
  const { task } = ctx;
  els.badges.querySelector(".badge")?.replaceWith(
    Object.assign(document.createElement("div"), { innerHTML: statusBadge(displayStatus(task)) }).firstElementChild
  );
  document.getElementById("td-lifecycle").innerHTML = timelineHTML(task);
  const glanceStatus = els.aside.querySelector(".aside-card li .badge");
  if (glanceStatus) glanceStatus.outerHTML = statusBadge(displayStatus(task));
  paintReviews(reviewState.get(task.id) ?? null);
  selectTab("reviews", { focus: false, updateHash: true });
  // The board and other pages read the control plane fresh next time.
  invalidateTasks();
}

document.addEventListener("click", (event) => {
  const target = event.target.closest("[data-review-open], [data-tab], [data-tab-link], [data-review-jump], [data-reviews-retry]");
  if (!target || !ctx.task) return;
  const { task, user } = ctx;
  if (target.matches("[data-review-open]")) {
    if (!user?.can_review) return;
    openReviewDrawer({
      task,
      user,
      state: reviewState.get(task.id) ?? null,
      onPublished: afterPublished,
    });
  } else if (target.matches("[data-tab]")) {
    selectTab(target.dataset.tab, { focus: false, updateHash: true });
  } else if (target.matches("[data-tab-link]")) {
    event.preventDefault();
    selectTab(target.dataset.tabLink, { updateHash: true });
  } else if (target.matches("[data-review-jump]")) {
    event.preventDefault();
    selectTab("reviews", { focus: false });
    history.replaceState(null, "", target.getAttribute("href"));
    document.querySelector(target.getAttribute("href"))?.scrollIntoView({ behavior: "smooth", block: "start" });
  } else if (target.matches("[data-reviews-retry]")) {
    target.disabled = true;
    loadReviews(task, { fresh: true }).then((state) => {
      reviewState.set(task.id, state);
      paintReviews(state);
    });
  }
});

// Arrow keys move between the two tabs (WAI-ARIA tabs pattern).
document.addEventListener("keydown", (event) => {
  const tab = event.target.closest?.(".td-tabs [role=tab]");
  if (!tab || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  event.preventDefault();
  const next = tab.dataset.tab === "proposal" ? "reviews" : "proposal";
  selectTab(event.key === "Home" ? "proposal" : event.key === "End" ? "reviews" : next, { focus: false, updateHash: true });
  document.querySelector(".td-tabs [aria-selected=true]")?.focus();
});

window.addEventListener("hashchange", () => selectTab(tabFromHash(), { focus: false }));

async function render() {
  const [task, user, canEditOnSite, site] = await Promise.all([
    key ? getTask(key) : null,
    currentUser().catch(() => null),
    editingAvailable(),
    getSite().catch(() => ({})),
  ]);
  if (!task) return notFound();
  const identifier = task.discussion_number ? `Proposal #${task.discussion_number}` : task.task_slug;
  document.title = `${identifier} · ${task.title} | AI4S-Benchmark`;
  const desc = document.querySelector('meta[name="description"]');
  if (desc) desc.setAttribute("content", String(task.problem || "").slice(0, 300));

  /* ---- Hero ---- */
  els.badges.innerHTML = `
    <span class="task-hero__id">${esc(identifier)}</span>
    ${statusBadge(displayStatus(task))}`;
  els.title.textContent = task.title;
  document.getElementById("td-lifecycle").innerHTML = timelineHTML(task);

  const metaBits = [
    `<span><span class="mono-label">Primary domain</span> &nbsp;<strong style="color:var(--navy);">${esc(String(task.domain ?? "").split(",")[0].trim())}</strong> <span class="hint">(all domains: ${esc(task.domain)})</span></span>`,
    task.revision_release ? `<span><span class="mono-label">Release</span> &nbsp;<span class="mono">${esc(task.revision_release)}</span></span>` : "",
    `<span><span class="mono-label">Updated</span> &nbsp;<span class="mono">${esc(formatDate(task.updated_at))}</span></span>`,
    `<span class="task-hero__field"><span class="mono-label">Field</span> &nbsp;${esc(task.field_name)}</span>`,
  ];
  els.meta.innerHTML = metaBits.filter(Boolean).join("");

  // Authors revise their proposal here on the site. The Discussion stays the
  // place review happens, so it is still linked — just no longer the way an
  // author is expected to make changes.
  const isAuthor = canEdit(task, user);
  const editsHere = isAuthor && canEditOnSite;

  const actions = [];
  // Reviewers review from here: the drawer opens beside the proposal.
  if (user?.can_review) {
    actions.push(
      `<button type="button" class="btn btn--primary" data-review-open data-review-label>Write a review</button>`
    );
  }
  if (editsHere) {
    actions.push(
      `<button type="button" class="btn btn--${user?.can_review ? "secondary" : "primary"}" id="td-edit">Edit proposal</button>`
    );
  }
  if (task.discussion_url) {
    actions.push(
      `<a class="btn btn--${editsHere || user?.can_review ? "secondary" : "primary"}" href="${esc(task.discussion_url)}" target="_blank" rel="noopener">${editsHere ? "View review Discussion" : "Open proposal Discussion"} <svg class="ext-arrow" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M4.75 11.25 11.25 4.75M5.9 4.75h5.35v5.35"/></svg></a>`
    );
  }
  // Set by the control plane when a proposal notification reached Discord.
  // Null for proposals older than that feature, so the link is conditional.
  if (task.discord_message_url) {
    actions.push(
      `<a class="btn btn--secondary" href="${esc(task.discord_message_url)}" target="_blank" rel="noopener" title="Opens this proposal's thread in the AI4S-Bench Discord server. Join the server first if you are not a member yet.">${ICONS.discord ?? ""}Discuss on Discord <svg class="ext-arrow" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M4.75 11.25 11.25 4.75M5.9 4.75h5.35v5.35"/></svg></a>`
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
  // Fallback only: if the control plane is not exposing the update route, an
  // author still needs some way to correct their own proposal.
  if (isAuthor && !canEditOnSite && task.discussion_url) {
    actions.push(
      `<a class="btn btn--secondary" href="${esc(task.discussion_url)}" target="_blank" rel="noopener">Edit on GitHub <svg class="ext-arrow" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M4.75 11.25 11.25 4.75M5.9 4.75h5.35v5.35"/></svg></a>`
    );
  }
  // One rule, stated the same way everywhere: revise here, talk on Discord,
  // and the Discussion is the structured record the pipeline reads.
  const discordJoin = site?.discord
    ? ` Not a member yet? <a href="${esc(site.discord)}" target="_blank" rel="noopener">Join the Discord server <svg class="ext-arrow" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M4.75 11.25 11.25 4.75M5.9 4.75h5.35v5.35"/></svg><span class="visually-hidden">(opens in a new tab)</span></a> first.`
    : "";
  const discordNote = task.discord_message_url
    ? ` Questions and discussion about this proposal happen on <strong>Discord</strong>.${discordJoin}`
    : "";
  if (editsHere) {
    actions.push(
      `<p class="task-hero__sync"><span class="task-hero__owner">You are the author of this proposal.</span> Use <strong>Edit proposal</strong> to revise it — your changes update this page and the Discussion together.${discordNote} The GitHub Discussion is the structured record for review and automation, so there is no need to edit it by hand.</p>`
    );
  } else if (task.discussion_url) {
    actions.push(
      `<p class="task-hero__sync">Content is synchronized from the proposal Discussion, the structured record for review and automation.${discordNote}${isAuthor ? ' <span class="task-hero__owner">You are the author of this proposal.</span> Edits made on GitHub appear here after the next sync.' : ""}</p>`
    );
  }
  els.actions.innerHTML = actions.join("");
  document.getElementById("td-edit")?.addEventListener("click", () => openEditor(task, user));

  /* ---- Main column ---- */
  const envRows = [
    `<div><dt>Software and tools</dt><dd>${richBlock(task.software)}</dd></div>`,
    `<div><dt>Dataset and artifacts</dt><dd>${richBlock(task.dataset)}</dd></div>`,
    `<div><dt>Requested compute</dt><dd>${richBlock(task.compute)}</dd></div>`,
  ].filter(Boolean);
  const envHTML = `<dl class="def-grid" style="grid-template-columns: 1fr;">${envRows.join("")}</dl>`;

  // Later stages (task PR, repository revision, agent runs) are not
  // settled yet, so they only appear once the control plane records them.
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
    : "";

  const revisionHTML = task.revision_id
    ? `<dl class="def-grid" style="grid-template-columns: 1fr;">
        <div><dt>Commit</dt><dd class="mono">${esc(task.revision_commit_sha)}</dd></div>
        <div><dt>Repository path</dt><dd class="mono">${esc(task.revision_task_path)}</dd></div>
        <div><dt>Resource requirements</dt><dd class="mono">${esc(JSON.stringify(task.revision_resource_requirements ?? {}))}</dd></div>
      </dl>`
    : "";

  const proposalHTML = [
    section("Scientific problem", para(task.problem)),
    section("Solvability", para(task.solvability)),
    section("References & resources", para(task.references)),
    section("Requested environment", envHTML),
    section("Expected workflow & outputs", para(task.workflow)),
    section("Proposed evaluation", para(task.evaluation) + `<div class="notice notice--rich" style="margin-top: var(--space-4);">${ICONS.info}<div><strong>Leakage risk</strong>${richBlock(task.leakage)}</div></div>`, "evaluation"),
    section("Repository revision", revisionHTML, "revision"),
    section("Agent results", resultsHTML, "results"),
  ].join("");

  const cached = reviewState.get(task.id) ?? null;
  els.main.innerHTML = `
    <div class="td-tabs" role="tablist" aria-label="Proposal and reviews">
      <button type="button" role="tab" id="tab-proposal" aria-controls="panel-proposal" data-tab="proposal">Proposal</button>
      <button type="button" role="tab" id="tab-reviews" aria-controls="panel-reviews" data-tab="reviews">Reviews <span class="td-tabs__count" data-review-count></span></button>
    </div>
    <div class="td-panel" id="panel-proposal" role="tabpanel" aria-labelledby="tab-proposal" tabindex="-1">${proposalHTML}</div>
    <section class="td-panel rv-panel" id="panel-reviews" role="tabpanel" aria-labelledby="reviews-h" tabindex="-1">${reviewsPanelHTML(task, cached, user)}</section>`;
  const firstPaint = !els.main.dataset.painted;
  els.main.dataset.painted = "1";
  // A shared #reviews link opens the Reviews tab and brings it into view.
  selectTab(tabFromHash(), { focus: firstPaint && tabFromHash() === "reviews" && location.hash === "#reviews" });
  void mountMath(els.main);

  /* ---- Aside ---- */
  const glance = [
    ["Status", statusBadge(displayStatus(task))],
    ["Assigned reviewer", task.assigned_reviewer_login ? `@${esc(task.assigned_reviewer_login)}` : "Unassigned"],
    ["Created", `<span class="mono">${esc(formatDate(task.created_at))}</span>`],
    ["Updated", `<span class="mono">${esc(formatDate(task.updated_at))}</span>`],
  ];

  // Several people may share a task; the first listed is the submitter (GitHub contact).
  const people = splitContributors(task.name, task.affiliation);
  const authorHTML = (people.length ? people : [{ name: task.name, affiliation: task.affiliation }])
    .map(
      (p, i) => `<div class="person"><span class="person__name">${esc(p.name)}</span>${p.affiliation ? `<span class="person__affil">${esc(p.affiliation)}</span>` : ""}${i === 0 ? `<span class="person__affil">@${esc(task.github)}</span>` : ""}</div>`
    )
    .join("");

  els.aside.innerHTML = `
    ${reviewsAsideHTML(cached, user)}
    <div class="aside-card">
      <h3>At a glance</h3>
      <ul>${glance.map(([k, v]) => `<li><span class="mono-label">${esc(k)}</span><span>${v}</span></li>`).join("")}</ul>
    </div>
    <div class="aside-card">
      <h3>${people.length > 1 ? "Task contributors" : "Task contributor"}</h3>
      ${authorHTML}
    </div>`;

  ctx = { task, user };
  paintReviews(cached);
  if (!cached) {
    loadReviews(task).then((state) => {
      reviewState.set(task.id, state);
      if (ctx.task === task) paintReviews(state);
    });
  }

  // Proposal checklist for the people who act on it: the author (to improve
  // the proposal) and reviewers (as hints, never a verdict).
  if (isAuthor || user?.can_review) {
    adviseOn(task, { selfId: task.id }).then((findings) => {
      els.aside.querySelector("#td-checklist")?.remove();
      const card = document.createElement("div");
      card.className = "aside-card";
      card.id = "td-checklist";
      card.innerHTML = checklistHTML(findings, {
        title: isAuthor ? "Your proposal checklist" : "Proposal checklist",
        compact: false,
        intro: isAuthor
          ? `Only you and reviewers see this.${editsHere ? " Use <strong>Edit proposal</strong> to address it." : ""}`
          : "Automated hints for reviewers, not a verdict.",
      });
      const reviewsCard = els.aside.querySelector("#td-reviews-card");
      if (reviewsCard) reviewsCard.after(card);
      else els.aside.prepend(card);
    });
  }
}

/* ---- On-site editing (authors only; the control plane enforces ownership) ---- */
function openEditor(task, user) {
  const host = document.createElement("div");
  host.id = "proposal-editor-host";
  els.main.replaceChildren(host);
  // Hiding the aside is not enough — its grid track stays declared, so the
  // layout must also collapse to one column or the editor keeps its width.
  const layout = els.main.closest(".task-layout");
  els.aside.hidden = true;
  layout?.classList.add("task-layout--editing");
  const restoreLayout = () => {
    els.aside.hidden = false;
    layout?.classList.remove("task-layout--editing");
  };
  mountEditor({
    task,
    user,
    root: host,
    onSaved: async () => {
      invalidateTasks();
      restoreLayout();
      await render();
      document.getElementById("task-detail-root")?.scrollIntoView({ behavior: "smooth", block: "start" });
    },
    onCancel: async () => {
      restoreLayout();
      await render();
    },
  });
  host.scrollIntoView({ behavior: "smooth", block: "start" });
}

render().catch((err) => {
  console.error("Task detail failed:", err);
  notFound();
});

document.addEventListener("ai4sbench:authchange", () => {
  closeReviewDrawer();
  void render();
});
