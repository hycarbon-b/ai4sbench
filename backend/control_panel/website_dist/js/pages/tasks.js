/* ============================================================
   AI4S-Benchmark · Task explorer
   Client-side search and filtering over proposal, review and revision data.
   Filters render only when the data actually contains values.
   ============================================================ */

import { getTasks, ROOT } from "../data.js";
import { taskCard, emptyState, esc } from "../components.js";

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

const STATUS_LABELS = {
  pending: "Pending Review",
  changes_requested: "Changes Requested",
  approved: "Approved",
  rejected: "Rejected",
};

function proposalDomains(task) {
  return String(task.domain ?? "")
    .split(",")
    .map((domain) => domain.trim())
    .filter(Boolean);
}

/* ---- Summary stats ---- */
function renderStats() {
  const domains = new Set(allTasks.flatMap(proposalDomains));
  const approved = allTasks.filter((task) => task.status === "approved").length;
  const pending = allTasks.filter((task) => task.status === "pending").length;

  const stats = [
    { value: allTasks.length, label: allTasks.length === 1 ? "Proposal" : "Proposals" },
    { value: domains.size, label: domains.size === 1 ? "Domain" : "Domains" },
    { value: approved, label: "Approved" },
    { value: pending, label: "Pending review" },
  ];
  els.stats.innerHTML = stats
    .map(
      (s) => `<div class="stat"><span class="stat__value">${esc(s.value)}</span><span class="stat__label">${esc(s.label)}</span></div>`
    )
    .join("");
}

/* ---- Data-driven filter selects ---- */
function buildFilters() {
  const defs = [
    { key: "status", label: "Status", values: uniq(allTasks.map((t) => t.status)), display: (v) => STATUS_LABELS[v] ?? v },
    { key: "domain", label: "Domain", values: uniq(allTasks.flatMap(proposalDomains)) },
    { key: "field_name", label: "Field", values: uniq(allTasks.map((task) => task.field_name)) },
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
          ${d.values.map((v) => `<option value="${esc(v)}">${esc(d.display ? d.display(v) : v)}</option>`).join("")}
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
  if (state.filters.status && task.status !== state.filters.status) return false;
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
