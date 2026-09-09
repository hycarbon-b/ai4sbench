/* ============================================================
   AI4S-Benchmark · Reviewer application model
   Pure functions (no DOM) that turn the reviewer form's answers
   into (a) the control plane's ReviewerApplication document and
   (b) a Markdown fallback. Kept DOM-free so it can be unit-tested
   with Node and reused by other pages — same shape as proposal.js.

   Backend contract:
     POST {control_plane_url}/api/v1/reviewers
     body: ReviewerApplication, schema tb-reviewer-application/v1
     expected: 201 with the created record, 422 on validation error.

   The reviewer endpoint is independent from task-proposal submission;
   its `role` field is the person's position and its slugs are
   lowercase-hyphen identifiers.
   ============================================================ */

import { slugAlpha } from "./proposal.js";

/** Public intake path on the control plane. */
export const REVIEWER_ENDPOINT = "/api/v1/reviewers";

export const SCHEMA_VERSION = "tb-reviewer-application/v1";

/** Minimum / maximum lengths. Keep in sync with the backend schema. */
export const LIMITS = {
  name: { min: 2, max: 200 },
  affiliation: { min: 2, max: 300 },
  email: { min: 5, max: 200 },
  github: { min: 0, max: 100 },
  position: { min: 0, max: 200 },
  field_name: { min: 0, max: 200 },
  background: { min: 60, max: 4000 },
};

const FIELD_LABELS = {
  name: "Name",
  affiliation: "Affiliation",
  email: "Email",
  github: "GitHub username",
  position: "Position",
  field_name: "Specific field",
  background: "Research background",
  domains: "Domains",
};

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const GITHUB_USER = /^[a-z\d](?:[a-z\d]|-(?=[a-z\d])){0,38}$/i;

/** Trim every known key, drop a leading @ from the GitHub handle. */
export function normalizeAnswers(raw = {}) {
  const out = {};
  for (const key of Object.keys(LIMITS)) out[key] = String(raw[key] ?? "").trim();
  out.github = out.github.replace(/^@/, "");
  out.domains = Array.isArray(raw.domains) ? raw.domains.map((d) => String(d).trim()).filter(Boolean) : [];
  return out;
}

/**
 * Validate answers. Returns { fieldName: "message" } — empty object means valid.
 * Messages are written for the researcher filling in the form.
 */
export function validateApplication(raw) {
  const a = normalizeAnswers(raw);
  const errors = {};

  for (const [key, { min, max }] of Object.entries(LIMITS)) {
    const value = a[key];
    if (min > 0 && value.length === 0) {
      errors[key] = `${FIELD_LABELS[key]} is required.`;
    } else if (value.length && value.length < min) {
      errors[key] = `${FIELD_LABELS[key]} needs at least ${min} characters (${value.length} so far).`;
    } else if (value.length > max) {
      errors[key] = `${FIELD_LABELS[key]} must be under ${max} characters (${value.length} now).`;
    }
  }

  if (!errors.email && !EMAIL.test(a.email)) {
    errors.email = "Please enter a valid email address.";
  }
  if (!errors.github && a.github && !GITHUB_USER.test(a.github)) {
    errors.github = "Enter a GitHub username without the @ (letters, digits, hyphens).";
  }
  if (a.domains.length === 0) {
    errors.domains = "Choose at least one domain you can review.";
  }
  return errors;
}

/** Build the control plane's ReviewerApplication from validated answers. */
export function buildReviewerDocument(raw) {
  const a = normalizeAnswers(raw);
  return {
    schema_version: SCHEMA_VERSION,
    name: a.name,
    affiliation: a.affiliation,
    email: a.email,
    github: a.github || null,
    role: a.position || null,
    domains: a.domains.map(slugAlpha),
    domains_display: a.domains,
    field: a.field_name ? slugAlpha(a.field_name) : null,
    subfield: a.field_name || null,
    research_background: a.background,
  };
}

/** Markdown rendering of the same answers (GitHub issue / email fallback). */
export function buildMarkdown(raw) {
  const a = normalizeAnswers(raw);
  const line = (label, value) => `**${label}:** ${value || "—"}\n`;
  return (
    `## Reviewer application: ${a.name || "(unnamed)"}\n\n` +
    line("Name", a.name) +
    line("Affiliation", a.affiliation) +
    line("Position", a.position) +
    line("Email", a.email) +
    line("GitHub", a.github ? `@${a.github}` : "") +
    line("Domains", a.domains.join(", ")) +
    line("Specific field", a.field_name) +
    `\n### Research background\n\n${a.background || "—"}\n\n` +
    `---\n*Submitted through the AI4S-Benchmark reviewer form (${SCHEMA_VERSION}).*\n`
  );
}
