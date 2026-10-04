export const REVIEW_SCHEMA_VERSION = "ai4sbench-proposal-review/v1";

const LIST_FIELDS = [
  "review_tags",
  "review_secondary_metrics",
  "review_baseline_results",
  "review_failure_modes",
];

export function reviewDraft(task) {
  const draft = {
    review_schema_version: task.review_schema_version || REVIEW_SCHEMA_VERSION,
    review_decision: task.review_decision || "",
    review_short_description: task.review_short_description || "",
    review_tags: task.review_tags || [],
    review_difficulty: task.review_difficulty || "",
    review_scientific_value: task.review_scientific_value || "",
    review_primary_metric: task.review_primary_metric || "",
    review_primary_metric_short: task.review_primary_metric_short || "",
    review_secondary_metrics: task.review_secondary_metrics || [],
    review_verification_method: task.review_verification_method || "",
    review_estimated_runtime: task.review_estimated_runtime || "",
    review_compute_budget: task.review_compute_budget || "",
    review_token_budget: task.review_token_budget || "",
    review_baseline_results: task.review_baseline_results || [],
    review_failure_modes: task.review_failure_modes || [],
    review_notes: task.review_notes || "",
  };
  for (const field of LIST_FIELDS) draft[field] = draft[field].join("\n");
  return draft;
}

function lines(value) {
  return String(value || "")
    .split(/\r?\n/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function optional(value) {
  const normalized = String(value || "").trim();
  return normalized || null;
}

export function reviewPayload(form) {
  const data = new FormData(form);
  return {
    review_schema_version: REVIEW_SCHEMA_VERSION,
    review_decision: String(data.get("review_decision") || ""),
    review_short_description: String(data.get("review_short_description") || "").trim(),
    review_tags: lines(data.get("review_tags")),
    review_difficulty: String(data.get("review_difficulty") || "").trim(),
    review_scientific_value: String(data.get("review_scientific_value") || "").trim(),
    review_primary_metric: String(data.get("review_primary_metric") || "").trim(),
    review_primary_metric_short: optional(data.get("review_primary_metric_short")),
    review_secondary_metrics: lines(data.get("review_secondary_metrics")),
    review_verification_method: String(data.get("review_verification_method") || "").trim(),
    review_estimated_runtime: optional(data.get("review_estimated_runtime")),
    review_compute_budget: optional(data.get("review_compute_budget")),
    review_token_budget: optional(data.get("review_token_budget")),
    review_baseline_results: lines(data.get("review_baseline_results")),
    review_failure_modes: lines(data.get("review_failure_modes")),
    review_notes: optional(data.get("review_notes")),
  };
}
