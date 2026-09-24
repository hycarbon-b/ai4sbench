/* ============================================================
   AI4S-Benchmark · Task lifecycle
   One task, one timeline: proposal → scientific review → task PR
   → agent evaluation → release. Everything here is derived from
   the public proposal-board item (proposal + latest review +
   latest task revision), so the board, the task page and the
   homepage all agree on where a task stands.

   Pure functions, no DOM — unit-tested with Node.

   Why derive instead of reading `status`: the control plane's
   `status` column is the proposal's own state and is "pending"
   for every proposal today, while a published review lands in
   the review_* columns and a task PR in the revision_* columns.
   Reading all three means a review or a PR shows up the moment
   it is recorded, whatever `status` says.
   ============================================================ */

/** The five stages, in order. `key` is stable; copy can change. */
export const STAGES = [
  {
    key: "proposal",
    label: "Proposal submitted",
    short: "Proposal",
    description: "The proposal is public as a GitHub Discussion and on this board.",
  },
  {
    key: "review",
    label: "Scientific review",
    short: "Review",
    description: "A domain reviewer assesses scientific value, verifiability and difficulty, and records a decision.",
  },
  {
    key: "implementation",
    label: "Task implementation",
    short: "Task PR",
    description: "An approved proposal becomes a task PR in the benchmark repository: environment, instruction, oracle solution and verifier.",
  },
  {
    key: "evaluation",
    label: "Agent evaluation",
    short: "Evaluation",
    description: "The merged task runs against frontier agents to calibrate difficulty and record failure modes.",
  },
  {
    key: "release",
    label: "Accepted & released",
    short: "Release",
    description: "The task is part of a versioned AI4S-Benchmark release.",
  },
];

/**
 * Display statuses, one per point in the lifecycle. Keys match the
 * badge classes and icons in components.js.
 */
export const STATUS_INFO = {
  pending: {
    label: "Pending Review",
    description: "Submitted and waiting for a domain reviewer's decision.",
  },
  changes_requested: {
    label: "Changes Requested",
    description: "A reviewer asked the author to revise the proposal. The author edits it on its task page.",
  },
  approved: {
    label: "Approved",
    description: "Approved by a reviewer. Next step: the author opens a task PR in the benchmark repository.",
  },
  rejected: {
    label: "Rejected",
    description: "A reviewer decided the proposal is not suitable for the benchmark in its current form.",
  },
  implementation: {
    label: "Task PR",
    description: "A task PR or repository revision is linked to this proposal.",
  },
  agent_testing: {
    label: "Agent Testing",
    description: "The task is being run against frontier agents to calibrate difficulty.",
  },
  released: {
    label: "Released",
    description: "The task is part of a versioned AI4S-Benchmark release.",
  },
};

/** Order used by the board's Stage filter and counts. */
export const STATUS_ORDER = [
  "pending",
  "changes_requested",
  "approved",
  "rejected",
  "implementation",
  "agent_testing",
  "released",
];

const DECISIONS = new Set(["approved", "changes_requested", "rejected"]);

const hasRevision = (t) => Boolean(t?.revision_id || t?.revision_pull_request_url || t?.revision_commit_sha);
const hasResults = (t) => Array.isArray(t?.revision_agent_results) && t.revision_agent_results.length > 0;
const hasRelease = (t) => Boolean(t?.revision_release);
const decisionOf = (t) => (DECISIONS.has(t?.review_decision) ? t.review_decision : null);

/**
 * The single status a task is shown with. Later evidence wins: a
 * release outranks agent results, which outrank a linked PR, which
 * outranks the review decision. A backend `status` we recognise is
 * used only when nothing more specific is recorded.
 */
export function displayStatus(task) {
  if (hasRelease(task)) return "released";
  if (hasResults(task)) return "agent_testing";
  if (hasRevision(task)) return "implementation";
  const decision = decisionOf(task);
  if (decision) return decision;
  const raw = String(task?.status ?? "").toLowerCase();
  if (raw === "released") return "released";
  if (raw === "agent_testing") return "agent_testing";
  if (raw === "approved" || raw === "verified") return "approved";
  if (raw === "rejected") return "rejected";
  if (raw === "changes_requested") return "changes_requested";
  return "pending";
}

/** Index (0–4) of the stage the task is currently in. */
export function currentStageIndex(task) {
  switch (displayStatus(task)) {
    case "released":
      return 4;
    case "agent_testing":
      return 3;
    case "implementation":
    case "approved":
      return 2;
    default:
      return 1; // pending, changes_requested, rejected: the review stage
  }
}

/** True for tasks counted as "approved" (approved or any later stage). */
export function isApproved(task) {
  return ["approved", "implementation", "agent_testing", "released"].includes(displayStatus(task));
}

/**
 * The full timeline for one task: each stage with a state and the
 * facts that belong to it. States:
 *   done      — finished
 *   current   — where the task is now (waiting on the next action)
 *   attention — current, but blocked on the author (changes requested)
 *   stopped   — the lifecycle ended here (rejected)
 *   upcoming  — not reached yet
 * Facts carry raw values (dates as ISO strings, URLs as given); the
 * page formats and escapes them.
 */
export function lifecycle(task) {
  const status = displayStatus(task);
  const at = currentStageIndex(task);
  const decision = decisionOf(task);
  const results = hasResults(task) ? task.revision_agent_results : [];

  const stateFor = (i) => {
    if (status === "rejected") return i === 0 ? "done" : i === 1 ? "stopped" : "upcoming";
    if (i < at) return "done";
    if (i > at) return "upcoming";
    if (status === "changes_requested" && i === 1) return "attention";
    return status === "released" && i === 4 ? "done" : "current";
  };

  const facts = [
    // 0 · Proposal
    {
      date: task?.created_at ?? null,
      note: task?.discussion_number ? `Discussion #${task.discussion_number}` : "Submitted",
      by: task?.github || null,
      link: task?.discussion_url ? { href: task.discussion_url, label: "Open Discussion", external: true } : null,
    },
    // 1 · Review
    decision
      ? {
          date: task.review_updated_at || task.review_created_at || null,
          note:
            decision === "approved"
              ? "Approved"
              : decision === "changes_requested"
                ? "Changes requested — the author revises the proposal"
                : "Not accepted",
          by: task.review_reviewer_login || null,
          link: task.review_comment_url ? { href: task.review_comment_url, label: "Read the review", external: true } : null,
        }
      : at > 1
        ? { date: null, note: "Completed", by: null, link: null }
        : { date: null, note: "Waiting for a domain reviewer", by: null, link: null },
    // 2 · Implementation
    hasRevision(task)
      ? {
          date: task.revision_created_at || null,
          note: task.revision_pull_request_url ? "Task PR linked" : "Repository revision linked",
          by: null,
          link: task.revision_pull_request_url
            ? { href: task.revision_pull_request_url, label: "Open task PR", external: true }
            : task.revision_repo_url && task.revision_commit_sha && task.revision_task_path
              ? {
                  href: `${task.revision_repo_url}/tree/${task.revision_commit_sha}/${task.revision_task_path}`,
                  label: "Open task files",
                  external: true,
                }
              : null,
        }
      : status === "approved"
        ? { date: null, note: "Next: the author opens a task PR", by: null, link: { href: "guide/", label: "How to build the task", internal: true } }
        : { date: null, note: "Opens after approval", by: null, link: null },
    // 3 · Evaluation
    results.length
      ? {
          date: latestDate(results.map((r) => r?.created_at)),
          note: `${results.length} agent ${results.length === 1 ? "run" : "runs"} recorded`,
          by: null,
          link: { href: "#results", label: "See results", anchor: true },
        }
      : hasRevision(task)
        ? { date: null, note: "Waiting for official agent runs", by: null, link: null }
        : { date: null, note: "After the task is merged", by: null, link: null },
    // 4 · Release
    hasRelease(task)
      ? { date: null, note: `Included in ${task.revision_release}`, by: null, link: { href: "releases/", label: "Releases", internal: true } }
      : { date: null, note: "Joins a versioned release once verified", by: null, link: null },
  ];

  return STAGES.map((stage, i) => ({ ...stage, index: i, state: stateFor(i), ...facts[i] }));
}

function latestDate(values) {
  const dates = values.filter(Boolean).map(String).sort();
  return dates.length ? dates[dates.length - 1] : null;
}
