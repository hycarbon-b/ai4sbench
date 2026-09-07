/* ============================================================
   AI4S-Benchmark · Proposal model
   Pure functions (no DOM) that turn the submission form's
   answers into (a) the control plane's ProposalSubmission payload and
   (b) a Markdown fallback. Kept DOM-free so it can be unit-
   tested with Node and reused by other pages.

   Backend contract: POST {control_plane_url}/api/v1/proposals
   with a `ProposalSubmission` — the control plane takes the raw
   answers and derives the task document itself, so the payload
   keys are exactly the form's field names. Limits below mirror
   that schema; keep them in sync.
   ============================================================ */

/** Minimum / maximum lengths enforced by the control plane (and by our form). */
export const LIMITS = {
  title: { min: 12, max: 160 },
  field_name: { min: 2, max: 160 },
  problem: { min: 80, max: 12000 },
  solvability: { min: 20, max: 12000 },
  references: { min: 40, max: 12000 },
  software: { min: 10, max: 5000 },
  compute: { min: 5, max: 900 },
  // Optional on the form: open questions have no workflow to prescribe. The
  // control plane still requires ≥ 20 characters, so an empty answer is sent
  // as WORKFLOW_NOT_SPECIFIED below.
  workflow: { min: 0, max: 12000 },
  dataset: { min: 10, max: 12000 },
  evaluation: { min: 20, max: 11000 },
  leakage: { min: 10, max: 900 },
  name: { min: 2, max: 200 },
  affiliation: { min: 0, max: 300 },
  github: { min: 1, max: 100 },
};

/** Which answers belong to which step (used for validation + navigation). */
export const STEP_FIELDS = [
  ["title", "domain", "field_name", "problem", "solvability", "references"],
  ["software", "dataset", "compute", "workflow"],
  ["evaluation", "leakage"],
  ["name", "affiliation", "github"],
];

const FIELD_LABELS = {
  title: "Task title",
  domain: "Domain",
  field_name: "Specific field",
  problem: "Problem specification",
  solvability: "Solvability",
  references: "References & resources",
  software: "Software and tools",
  dataset: "Dataset & artifacts",
  compute: "Computation resources",
  workflow: "Expected workflow & outputs",
  evaluation: "Evaluation method",
  leakage: "Cheating & leakage risk",
  name: "Name",
  affiliation: "Institution / affiliation",
  github: "GitHub username",
};

/** kebab-case slug: letters, digits and single hyphens (task_slug pattern). */
export function slugify(text, maxLength = 80) {
  return String(text ?? "")
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .replace(/-{2,}/g, "-")
    .slice(0, maxLength)
    .replace(/-+$/g, "");
}

/** Letters-and-hyphens slug for the internal task field identifier. */
export function slugAlpha(text) {
  return String(text ?? "")
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .replace(/-{2,}/g, "-")
    .slice(0, 79);
}

const ALPHA_SLUG = /^[a-z][a-z-]{1,78}$/;
const TASK_SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

/** Sent in place of an empty (optional) workflow answer. */
export const WORKFLOW_NOT_SPECIFIED = "Not specified: this is an open question without a prescribed workflow.";

/** Domains are multi-select; the control plane takes one free-text string. */
export const DOMAIN_SEPARATOR = ", ";

/** Normalise raw form values: trim strings, default missing keys to "". */
export function normalizeAnswers(raw = {}) {
  const out = {};
  for (const key of Object.keys(LIMITS)) out[key] = String(raw[key] ?? "").trim();
  // domain may arrive as an array (checkbox group) or an already-joined string
  const domains = Array.isArray(raw.domain) ? raw.domain : [raw.domain];
  out.domain = domains.map((d) => String(d ?? "").trim()).filter(Boolean).join(DOMAIN_SEPARATOR);
  // People paste handles with the @ still attached; accept it either way.
  out.github = out.github.replace(/^@/, "");
  return out;
}

/**
 * Validate answers. Returns { fieldName: "message" } — empty object means valid.
 * Messages are written for the scientist filling in the form.
 */
export function validateAnswers(raw) {
  const a = normalizeAnswers(raw);
  const errors = {};

  for (const [key, { min, max }] of Object.entries(LIMITS)) {
    const value = a[key];
    if (min > 0 && value.length === 0) {
      errors[key] = `${FIELD_LABELS[key]} is required.`;
    } else if (value.length < min) {
      errors[key] = `${FIELD_LABELS[key]} needs at least ${min} characters (${value.length} so far).`;
    } else if (value.length > max) {
      errors[key] = `${FIELD_LABELS[key]} must be under ${max} characters (${value.length} now).`;
    }
  }

  if (!a.domain) {
    errors.domain = "Choose at least one domain.";
  }
  if (!errors.field_name && !ALPHA_SLUG.test(slugAlpha(a.field_name))) {
    errors.field_name = "Specific field must contain at least two letters (e.g. “Coastal oceanography”).";
  }
  if (!errors.title && !TASK_SLUG.test(slugify(a.title))) {
    errors.title = "Title must contain letters or digits so we can derive an identifier.";
  }
  if (!errors.github && !/^[a-z\d](?:[a-z\d]|-(?=[a-z\d])){0,38}$/i.test(a.github)) {
    errors.github = "Enter a GitHub username without the @ (letters, digits, hyphens).";
  }
  return errors;
}

/** Step index of the first invalid field, or -1 when everything is valid. */
export function firstInvalidStep(errors) {
  const keys = Object.keys(errors);
  if (keys.length === 0) return -1;
  return STEP_FIELDS.findIndex((fields) => fields.some((f) => keys.includes(f)));
}

/** Exactly the fields ProposalSubmission accepts, in schema order. */
export const SUBMISSION_FIELDS = [
  "title",
  "domain",
  "field_name",
  "problem",
  "solvability",
  "references",
  "software",
  "dataset",
  "compute",
  "workflow",
  "evaluation",
  "leakage",
  "name",
  "affiliation",
  "github",
];

/**
 * Build the control plane's ProposalSubmission from validated answers.
 * Only schema fields are sent: the backend rejects unknown keys, and it
 * derives slugs and the task document on its own.
 */
export function buildProposalSubmission(raw) {
  const a = normalizeAnswers(raw);
  const payload = {};
  for (const key of SUBMISSION_FIELDS) payload[key] = a[key] ?? "";
  payload.github = payload.github.replace(/^@/, "");
  if (!payload.workflow) payload.workflow = WORKFLOW_NOT_SPECIFIED;
  return payload;
}

/** Markdown rendering of the same answers (offline / email fallback). */
export function buildMarkdown(raw) {
  const a = normalizeAnswers(raw);
  const block = (heading, text) => `### ${heading}\n\n${text || "—"}\n\n`;
  const contributor = [a.name, a.affiliation, a.github ? `@${a.github}` : ""].filter(Boolean).join(" · ");
  return (
    `## Task Proposal: ${a.title || "(untitled)"}\n\n` +
    `**Domains:** ${a.domain || "—"}${a.field_name ? ` · ${a.field_name}` : ""}\n` +
    `**Identifier:** \`${slugify(a.title) || "—"}\`\n\n` +
    `## 1 · Scientific problem\n\n` +
    block("Problem specification", a.problem) +
    block("Solvability", a.solvability) +
    block("References & resources", a.references) +
    `## 2 · Environment\n\n` +
    block("Software and tools", a.software) +
    block("Dataset & artifacts", a.dataset) +
    block("Computation resources (time and device)", a.compute) +
    block("Expected workflow & outputs", a.workflow || WORKFLOW_NOT_SPECIFIED) +
    `## 3 · Evaluation\n\n` +
    block("How will this task be evaluated?", a.evaluation) +
    block("Risk of cheating and leakage", a.leakage) +
    `## 4 · Contributor\n\n${contributor || "—"}\n\n` +
    `---\n*Drafted with the AI4S-Benchmark proposal form.*\n`
  );
}
