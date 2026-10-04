/* ============================================================
   AI4S-Benchmark · Task explorer
   Client-side search and filtering over proposal, review and revision data.
   Filters render only when the data actually contains values.
   ============================================================ */

import { getTasks, ROOT } from "../data.js?v=20260921-3";
import { taskCard, emptyState, esc } from "../components.js?v=20260921-3";
import { displayStatus, isApproved, STATUS_INFO, STATUS_ORDER } from "../lifecycle.js?v=20260921-3";
import { mountMath } from "../richtext.js?v=20260921-3";

const state = {
  query: "",
  filters: {}, // key -> selected value
  sort: "updated",
};

let allTasks = [];

const els = {
  stats: document.getElementById("task-stats"),
  search: document.getElementById("task-search"),
  filters: document.getElementById("task-filters"),
  sort: document.getElementById("task-sort"),
  clear: document.getElementById("filter-clear"),
  count: document.getElementById("task-count"),
  list: document.getElementById("task-list"),
};

/* Long free-text field names stay readable in the Field filter. */
const FIELD_LABEL_MAX = 56;
const shorten = (text) => (text.length > FIELD_LABEL_MAX ? `${text.slice(0, FIELD_LABEL_MAX - 1).trimEnd()}…` : text);

function proposalDomains(task) {
  return String(task.domain ?? "")
    .split(",")
    .map((domain) => domain.trim())
    .filter(Boolean);
}

/* ---- Summary stats ---- */
function renderStats() {
  const domains = new Set(allTasks.flatMap(proposalDomains));
  // Counted from the lifecycle, so a published review or a linked PR is
  // reflected even while the proposal's own `status` column still says pending.
  const approved = allTasks.filter(isApproved).length;
  const pending = allTasks.filter((task) => displayStatus(task) === "pending").length;

  const stats = [
    { value: allTasks.length, label: allTasks.length === 1 ? "Proposal" : "Proposals", title: "Every valid proposal on the board, whatever its stage." },
    { value: domains.size, label: domains.size === 1 ? "Domain" : "Domains", title: "Distinct scientific domains across all proposals. A proposal can span several." },
    { value: approved, label: "Approved", title: "Approved by a reviewer, including tasks already in implementation, evaluation or a release." },
    { value: pending, label: "Pending review", title: "Waiting for a reviewer's first decision." },
  ];
  els.stats.innerHTML = stats
    .map(
      (s) => `<div class="stat" title="${esc(s.title)}"><span class="stat__value">${esc(s.value)}</span><span class="stat__label">${esc(s.label)}</span></div>`
    )
    .join("");
}

/* ---- Data-driven filter selects ---- */
function buildFilters() {
  // Every stage is listed with its count, so the filter also shows the
  // shape of the pipeline — including stages no task has reached yet.
  const stageCounts = new Map(STATUS_ORDER.map((key) => [key, 0]));
  allTasks.forEach((task) => stageCounts.set(displayStatus(task), (stageCounts.get(displayStatus(task)) ?? 0) + 1));
  const defs = [
    {
      key: "stage",
      label: "Stage",
      values: STATUS_ORDER,
      display: (v) => `${STATUS_INFO[v].label} (${stageCounts.get(v)})`,
      title: (v) => STATUS_INFO[v].description,
      disabled: (v) => stageCounts.get(v) === 0,
    },
    { key: "domain", label: "Domain", values: uniq(allTasks.flatMap(proposalDomains)) },
    { key: "field_name", label: "Field", values: uniq(allTasks.map((task) => task.field_name)), display: shorten, title: (v) => v },
    { key: "review_difficulty", label: "Difficulty", values: uniq(allTasks.map((task) => task.review_difficulty)) },
    { key: "revision_release", label: "Release", values: uniq(allTasks.map((task) => task.revision_release)) },
  ];

  els.filters.innerHTML = defs
    .filter((d) => d.values.length > 0)
    .map(
      (d) => `<div class="select-control">
        <label for="filter-${d.key}">${esc(d.label)}</label>
        <select id="filter-${d.key}" data-filter="${d.key}">
          <option value="">All</option>
          ${d.values.map((v) => `<option value="${esc(v)}"${d.title ? ` title="${esc(d.title(v))}"` : ""}${d.disabled?.(v) ? " disabled" : ""}>${esc(d.display ? d.display(v) : v)}</option>`).join("")}
        </select>
      </div>`
    )
    .join("");

  els.filters.querySelectorAll("select").forEach((sel) => {
    sel.addEventListener("change", () => {
      state.filters[sel.dataset.filter] = sel.value;
      render();
    });
  });
}

function uniq(arr) {
  return [...new Set(arr.filter((v) => v != null && v !== ""))].sort();
}

/* ---- Filtering pipeline ---- */
function matches(task) {
  const q = state.query.trim().toLowerCase();
  if (q) {
    const haystack = [
      task.title,
      task.id,
      task.task_slug,
      task.problem,
      task.domain,
      task.field_name,
      task.name,
      task.github,
      ...(task.review_tags ?? []),
    ]
      .join(" ")
      .toLowerCase();
    if (!haystack.includes(q)) return false;
  }
  if (state.filters.stage && displayStatus(task) !== state.filters.stage) return false;
  if (state.filters.domain && !proposalDomains(task).includes(state.filters.domain)) return false;
  if (state.filters.field_name && task.field_name !== state.filters.field_name) return false;
  if (state.filters.review_difficulty && task.review_difficulty !== state.filters.review_difficulty) return false;
  if (state.filters.revision_release && task.revision_release !== state.filters.revision_release) return false;
  return true;
}

function sortTasks(tasks) {
  const sorted = [...tasks];
  if (state.sort === "updated") sorted.sort((a, b) => (b.updated_at ?? "").localeCompare(a.updated_at ?? ""));
  if (state.sort === "added") sorted.sort((a, b) => (b.created_at ?? "").localeCompare(a.created_at ?? ""));
  if (state.sort === "alpha") sorted.sort((a, b) => a.title.localeCompare(b.title));
  return sorted;
}

function anyFilterActive() {
  return (
    state.query.trim() !== "" ||
    Object.values(state.filters).some((v) => v)
  );
}

function render() {
  const visible = sortTasks(allTasks.filter(matches));
  els.count.textContent = `${visible.length} of ${allTasks.length} proposals`;
  els.clear.hidden = !anyFilterActive();

  if (visible.length === 0) {
    els.list.innerHTML = `<div style="grid-column: 1 / -1;">${emptyState({
      title: allTasks.length === 0 ? "No proposals yet" : "No proposals match these filters",
      text:
        allTasks.length === 0
          ? "Submit the first scientific proposal to help define the benchmark."
          : "Try broadening your search or clearing a filter.",
      actionsHTML:
        allTasks.length === 0
          ? `<a class="btn btn--primary" href="${ROOT}submit/">Submit a Task</a>`
          : `<button type="button" class="btn btn--secondary" id="empty-clear">Clear all filters</button>`,
    })}</div>`;
    document.getElementById("empty-clear")?.addEventListener("click", clearFilters);
    return;
  }
  els.list.innerHTML = visible.map(taskCard).join("");
  void mountMath(els.list);
}

function clearFilters() {
  state.query = "";
  state.filters = {};
  els.search.value = "";
  els.filters.querySelectorAll("select").forEach((s) => (s.value = ""));
  render();
}

/* ---- Events ---- */
els.search.addEventListener("input", () => {
  state.query = els.search.value;
  render();
});
els.sort.addEventListener("change", () => {
  state.sort = els.sort.value;
  render();
});
els.clear.addEventListener("click", clearFilters);

/* ---- Init ---- */
getTasks()
  .then((tasks) => {
    allTasks = tasks;
    renderStats();
    buildFilters();
    render();
  })
  .catch((err) => {
    console.error("Task explorer failed to load:", err);
    els.list.innerHTML = `<div style="grid-column: 1 / -1;">${emptyState({
      title: "Proposals could not be loaded",
      text: "The public proposal service could not be reached. Refresh the page or try again later.",
    })}</div>`;
  });
