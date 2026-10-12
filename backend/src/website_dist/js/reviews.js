/* ============================================================
   AI4S-Benchmark · Proposal reviews (pure, no DOM)

   The reviewer form asks what a reviewer is actually qualified to
   judge — a recommendation, a few 1–5 scores, and comments — and
   nothing the proposal author already defined (metrics,
   verification, runtime estimates, tags).

   Transport: the control plane still accepts only its v1 review
   schema (ai4sbench-proposal-review/v1), which was modelled on task
   metadata and requires a short description, tags, a primary metric
   and a verification method. Until it grows a reviewer-shaped
   schema, `toV1Payload()` maps a review onto v1 so every heading of
   the Discussion reply still says something true, and appends a
   hidden `<!-- ai4sbench-review:v2 … -->` marker carrying the scores
   so this site can read the review back exactly.

   Reading: every review is a reply on the proposal's GitHub
   Discussion. `parseReviewComment()` reads both the v2 layout written
   by this form and legacy v1 replies. The control plane stores only
   the latest review, so the list of all reviews comes from the
   Discussion itself (see js/discussion.js).

   Unit-tested with Node: tests/reviews.test.mjs.
   ============================================================ */

export const REVIEW_MARKER = "<!-- ai4sbench-proposal-review:v1 -->";
const V2_MARKER_RE = /<!--\s*ai4sbench-review:v2\s+([A-Za-z0-9+/=]+)\s*-->/;

export const DECISIONS = [
  { value: "approved", label: "Approve", past: "Approved", note: "Ready to be built as a task" },
  { value: "changes_requested", label: "Request changes", past: "Changes requested", note: "Promising, but the author must revise it" },
  { value: "rejected", label: "Reject", past: "Rejected", note: "Not suitable for the benchmark" },
];
export const DECISION_LABEL = Object.fromEntries(DECISIONS.map((d) => [d.value, d.past]));

/**
 * Scored criteria, 1–5, every one optional. `quality: true` criteria
 * are "higher is better" and feed the quality average; difficulty is
 * a calibration, not a quality, so it is reported on its own.
 * Anchors are shown to the reviewer for the selected score.
 */
export const CRITERIA = [
  {
    key: "value",
    label: "Scientific value",
    question: "Would a working solution matter to researchers in this field?",
    quality: true,
    anchors: ["Toy or textbook", "Minor interest", "Useful", "Significant", "Real research need"],
  },
  {
    key: "clarity",
    label: "Well-specified",
    question: "Is the task specified precisely enough that an agent knows what counts as success?",
    quality: true,
    anchors: ["Ambiguous", "Major gaps", "Some gaps", "Clear", "Unambiguous"],
  },
  {
    key: "verifiability",
    label: "Verifiability",
    question: "Can success be checked objectively, without rejecting valid alternative solutions?",
    quality: true,
    anchors: ["Subjective", "Needs judgement", "Partly checkable", "Robust check", "Airtight check"],
  },
  {
    key: "feasibility",
    label: "Solvable & feasible",
    question: "Is there a plausible solution, and are the software, data, compute and runtime realistic?",
    quality: true,
    anchors: ["Not solvable", "Major obstacles", "Doable with work", "Realistic", "Ready to build"],
  },
  {
    key: "leakage",
    label: "Leakage resistance",
    question: "Is it protected against memorised answers, web look-up and shortcuts?",
    quality: true,
    anchors: ["Answer is public", "Easily shortcut", "Some risk", "Well protected", "No known risk"],
  },
  {
    key: "difficulty",
    label: "Difficulty",
    question: "How hard is this for today's frontier agents — for a good reason, not a trick?",
    quality: false,
    anchors: ["Trivial", "Easy", "Moderate", "Hard", "Beyond current agents"],
  },
];
export const CRITERION = Object.fromEntries(CRITERIA.map((c) => [c.key, c]));

export const CONFIDENCE = {
  label: "Your confidence",
  question: "How well does this sit within your expertise?",
  anchors: ["Educated guess", "Some familiarity", "Fairly confident", "Confident", "Expert, certain"],
};

/** Runtime is estimated by the author; the reviewer only judges it. */
export const RUNTIME_VERDICTS = [
  { value: "reasonable", label: "Reasonable" },
  { value: "too_long", label: "Too long or too costly" },
  { value: "too_short", label: "Too short to be meaningful" },
  { value: "unclear", label: "Not enough information" },
];
const RUNTIME_LABEL = Object.fromEntries(RUNTIME_VERDICTS.map((v) => [v.value, v.label]));

/** Difficulty calibrated by human effort, as SWE-bench Verified does. */
export const EXPERT_TIME = [
  { value: "lt1h", label: "Under 1 hour" },
  { value: "1to4h", label: "1–4 hours" },
  { value: "1to3d", label: "1–3 days" },
  { value: "gt3d", label: "More than 3 days" },
];
export const EXPERT_TIME_LABEL = Object.fromEntries(EXPERT_TIME.map((v) => [v.value, v.label]));

export const LIMITS = { general: 5000, environment: 2000, evaluation: 4000 };
export const MIN_GENERAL = 10;

/** An empty review: every reviewer starts from this, never from someone else's. */
export function emptyReview() {
  return {
    decision: "",
    scores: {},
    confidence: null,
    runtime: "",
    expertTime: "",
    general: "",
    environment: "",
    evaluation: "",
  };
}

const clampScore = (n) => {
  const v = Number(n);
  return Number.isInteger(v) && v >= 1 && v <= 5 ? v : null;
};

/** Keep only known, valid values; used for drafts and parsed markers alike. */
export function normalizeReview(input = {}) {
  const out = emptyReview();
  if (DECISION_LABEL[input.decision]) out.decision = input.decision;
  for (const c of CRITERIA) {
    const v = clampScore(input.scores?.[c.key]);
    if (v) out.scores[c.key] = v;
  }
  out.confidence = clampScore(input.confidence);
  if (RUNTIME_LABEL[input.runtime]) out.runtime = input.runtime;
  if (EXPERT_TIME_LABEL[input.expertTime]) out.expertTime = input.expertTime;
  for (const k of ["general", "environment", "evaluation"]) {
    out[k] = String(input[k] ?? "").replace(/\r\n?/g, "\n").trim().slice(0, LIMITS[k]);
  }
  return out;
}

/** Field → message for anything that blocks publishing. Empty object = valid. */
export function validateReview(review) {
  const errors = {};
  if (!review.decision) errors.decision = "Choose a recommendation.";
  if (review.general.trim().length < MIN_GENERAL) {
    errors.general = `Write at least ${MIN_GENERAL} characters of general comments.`;
  }
  return errors;
}

/** Mean of the quality criteria that were scored, or null. */
export function qualityScore(scores = {}) {
  const vals = CRITERIA.filter((c) => c.quality && scores[c.key]).map((c) => scores[c.key]);
  return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
}

export const scoreLabel = (key, n) => {
  const c = key === "confidence" ? CONFIDENCE : CRITERION[key];
  return c && n ? `${n}/5 · ${c.anchors[n - 1]}` : "";
};

/* ---- v1 transport ------------------------------------------ */

/* A line starting with "#" would end the section it sits in when the
   control plane parses the reply back, and "Decision:" at line start is
   the decision field — so both are escaped in free text. */
function safeText(text) {
  return String(text || "")
    .split("\n")
    .map((line) => line.replace(/^(\s*)(#{1,6}\s)/, "$1\\$2").replace(/^(\s*)(Decision\s*:)/i, "$1\\$2"))
    .join("\n")
    .trim();
}

function firstSentence(text, max = 240) {
  const flat = String(text || "").replace(/\s+/g, " ").trim();
  const m = flat.match(/^(.{20,}?[.!?])(\s|$)/);
  const s = m ? m[1] : flat;
  return s.length > max ? `${s.slice(0, max - 1).trimEnd()}…` : s;
}

function b64encode(str) {
  const bytes = new TextEncoder().encode(str);
  let bin = "";
  bytes.forEach((b) => (bin += String.fromCharCode(b)));
  return btoa(bin);
}
function b64decode(str) {
  const bin = atob(str);
  return new TextDecoder().decode(Uint8Array.from(bin, (ch) => ch.charCodeAt(0)));
}

/** One line for Discord and the Discussion: recommendation, scores, gist. */
export function summaryLine(review) {
  const bits = [`Recommendation: ${DECISION_LABEL[review.decision] ?? "—"}`];
  const q = qualityScore(review.scores);
  if (q) bits.push(`quality ${q.toFixed(1)}/5`);
  if (review.scores.difficulty) bits.push(`difficulty ${review.scores.difficulty}/5`);
  if (review.confidence) bits.push(`confidence ${review.confidence}/5`);
  return `${bits.join(" · ")}. ${firstSentence(review.general)}`.slice(0, 2000);
}

function scoreTable(review) {
  const rows = CRITERIA.filter((c) => review.scores[c.key]).map(
    (c) => `| ${c.label} | ${review.scores[c.key]}/5 · ${c.anchors[review.scores[c.key] - 1]} |`
  );
  if (review.expertTime) rows.push(`| Time an expert would need | ${EXPERT_TIME_LABEL[review.expertTime]} |`);
  if (review.confidence) rows.push(`| Reviewer confidence | ${scoreLabel("confidence", review.confidence)} |`);
  return rows.length ? ["**Scores**", "", "| Criterion | Score |", "| --- | --- |", ...rows, ""] : [];
}

/**
 * The control plane's v1 ProposalReview body for this review.
 * `task` supplies the tags (its domain and field) that v1 requires.
 */
export function toV1Payload(input, task = {}) {
  const review = normalizeReview(input);
  const tags = [task.domain, task.field_name].map((t) => String(t || "").trim()).filter(Boolean);
  const verification = [
    review.scores.verifiability ? `Verifiability: ${scoreLabel("verifiability", review.scores.verifiability)}.` : "",
    review.scores.leakage ? `Leakage resistance: ${scoreLabel("leakage", review.scores.leakage)}.` : "",
    safeText(review.evaluation),
  ]
    .filter(Boolean)
    .join("\n\n");

  const marker = {
    v: 2,
    decision: review.decision,
    scores: review.scores,
    confidence: review.confidence,
    runtime: review.runtime || null,
    expertTime: review.expertTime || null,
  };
  const notes = [
    ...scoreTable(review),
    "**General comments**",
    "",
    safeText(review.general),
    "",
    ...(review.environment || review.runtime
      ? [
          "**Environment and resources**",
          "",
          ...(review.runtime ? [`Runtime estimate: ${RUNTIME_LABEL[review.runtime]}`, ""] : []),
          ...(review.environment ? [safeText(review.environment), ""] : []),
        ]
      : []),
    `<!-- ai4sbench-review:v2 ${b64encode(JSON.stringify(marker))} -->`,
  ].join("\n");

  return {
    review_schema_version: "ai4sbench-proposal-review/v1",
    review_decision: review.decision,
    review_short_description: summaryLine(review),
    review_tags: tags.length ? tags : ["general"],
    review_difficulty:
      [review.scores.difficulty ? scoreLabel("difficulty", review.scores.difficulty) : "", review.expertTime ? `expert time ${EXPERT_TIME_LABEL[review.expertTime]}` : ""]
        .filter(Boolean)
        .join(" · ") || "Not rated",
    review_scientific_value: review.scores.value
      ? `Scientific value: ${scoreLabel("value", review.scores.value)}.`
      : "Not scored separately; see the general comments.",
    review_primary_metric: "As defined in the proposal",
    review_primary_metric_short: null,
    review_secondary_metrics: [],
    review_verification_method: verification.length >= 20 ? verification : "No separate comment on evaluation; see the general comments.",
    review_estimated_runtime: review.runtime ? RUNTIME_LABEL[review.runtime] : null,
    review_compute_budget: null,
    review_token_budget: null,
    review_baseline_results: [],
    review_failure_modes: [],
    review_notes: notes,
  };
}

/* ---- Reading replies back ---------------------------------- */

function section(body, heading) {
  const re = new RegExp(`^###\\s*${heading}\\s*$\\n+([\\s\\S]*?)(?=^###\\s|(?![\\s\\S]))`, "im");
  const m = String(body || "").match(re);
  return m ? m[1].trim() : "";
}
function listSection(body, heading) {
  return section(body, heading)
    .split("\n")
    .map((l) => l.match(/^\s*[-*]\s+(.+?)\s*$/)?.[1])
    .filter(Boolean);
}
const unescape = (text) => String(text || "").replace(/^(\s*)\\(#{1,6}\s|Decision\s*:)/gim, "$1$2");

/** Text under a bold label inside Review Notes, up to the next bold label or marker. */
function block(notes, label) {
  const re = new RegExp(`^\\*\\*${label}\\*\\*\\s*$\\n+([\\s\\S]*?)(?=^\\*\\*[^*\\n]+\\*\\*\\s*$|<!--\\s*ai4sbench-review|(?![\\s\\S]))`, "im");
  const m = String(notes || "").match(re);
  return m ? m[1].trim() : "";
}

const isPlaceholder = (text) =>
  /^(Not scored separately|No separate comment on evaluation|As defined in the proposal|Not rated)/i.test(String(text || "").trim());

/**
 * Parse one Discussion reply. Returns null when the reply is not a
 * review. `format` is "v2" (written by this form) or "v1" (legacy).
 */
export function parseReviewComment(body) {
  const text = String(body || "").replace(/\r\n?/g, "\n");
  if (!text.includes(REVIEW_MARKER)) return null;
  const decision = (text.match(/^Decision\s*:\s*(\S+)\s*$/im)?.[1] || "").trim();
  const notes = section(text, "Review Notes");
  const markerMatch = notes.match(V2_MARKER_RE) || text.match(V2_MARKER_RE);

  if (markerMatch) {
    let data = {};
    try {
      data = JSON.parse(b64decode(markerMatch[1]));
    } catch {
      data = {};
    }
    const verification = section(text, "Verification Method");
    const evaluation = isPlaceholder(verification)
      ? ""
      : verification
          .split("\n\n")
          .filter((p) => !/^(Verifiability|Leakage resistance):\s*\d\/5/.test(p.trim()))
          .join("\n\n")
          .trim();
    const envBlock = block(notes, "Environment and resources").replace(/^Runtime estimate:.*$/m, "").trim();
    return {
      format: "v2",
      ...normalizeReview({
        decision: decision || data.decision,
        scores: data.scores,
        confidence: data.confidence,
        runtime: data.runtime,
        expertTime: data.expertTime,
        general: unescape(block(notes, "General comments")),
        environment: unescape(envBlock),
        evaluation: unescape(evaluation),
      }),
    };
  }

  // Legacy v1 reply: keep what the reviewer wrote, drop empty headings.
  const legacy = [
    ["Summary", section(text, "Short Description")],
    ["Scientific value", section(text, "Scientific Value")],
    ["Primary metric", section(text, "Primary Metric")],
    ["Verification", section(text, "Verification Method")],
    ["Estimated runtime", section(text, "Estimated Runtime")],
    ["Compute budget", section(text, "Compute Budget")],
    ["Notes", section(text, "Review Notes")],
  ].filter(([, v]) => v);
  const difficulty = section(text, "Difficulty");
  return {
    format: "v1",
    ...emptyReview(),
    decision: DECISION_LABEL[decision] ? decision : "",
    general: legacy.map(([k, v]) => `**${k}.** ${v}`).join("\n\n"),
    legacyDifficulty: difficulty,
    legacyTags: listSection(text, "Tags"),
  };
}

/* ---- Many reviews ------------------------------------------ */

/**
 * Group parsed reviews by reviewer; each reviewer's newest counts,
 * older ones are kept as history. Input items: { login, created_at,
 * updated_at, url, review }. Output newest-first.
 */
export function latestPerReviewer(items) {
  const byLogin = new Map();
  const sorted = [...items].sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));
  for (const item of sorted) {
    const key = String(item.login || "").toLowerCase();
    if (!byLogin.has(key)) byLogin.set(key, { ...item, history: [] });
    else byLogin.get(key).history.push(item);
  }
  return [...byLogin.values()];
}

/** Decision tally and per-criterion means over the current reviews. */
export function aggregate(current) {
  const tally = Object.fromEntries(DECISIONS.map((d) => [d.value, 0]));
  current.forEach((r) => {
    if (tally[r.review.decision] !== undefined) tally[r.review.decision] += 1;
  });
  const criteria = [...CRITERIA.map((c) => c.key), "confidence"].map((key) => {
    const vals = current
      .map((r) => (key === "confidence" ? r.review.confidence : r.review.scores?.[key]))
      .filter((v) => Number.isInteger(v));
    return {
      key,
      n: vals.length,
      mean: vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null,
      // Conservative view (SWE-bench Verified keeps the most severe label).
      min: vals.length ? Math.min(...vals) : null,
    };
  });
  const qualities = current.map((r) => qualityScore(r.review.scores)).filter((v) => v !== null);
  return {
    count: current.length,
    tally,
    criteria,
    quality: qualities.length ? qualities.reduce((a, b) => a + b, 0) / qualities.length : null,
  };
}
