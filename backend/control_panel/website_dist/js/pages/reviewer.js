/* ============================================================
   AI4S-Benchmark · Reviewer application page

   Submission is endpoint-first: it POSTs a ReviewerApplication to
   the control plane. That endpoint is not deployed yet, so when it
   answers 404/405/501 the form falls back to opening the same
   application as a prefilled GitHub issue. Once the endpoint ships
   nothing here needs changing — the POST simply starts succeeding.
   ============================================================ */

import { getSite } from "../data.js";
import { esc } from "../components.js";
import { controlPlaneFetch } from "../app.js";
import {
  LIMITS,
  REVIEWER_ENDPOINT,
  buildMarkdown,
  buildReviewerDocument,
  validateApplication,
} from "../reviewer.js";

const form = document.getElementById("reviewer-form");
const statusEl = document.getElementById("reviewer-status");
const submitBtn = document.getElementById("reviewer-submit");
const domainBox = document.getElementById("domain-options");
const backgroundEl = document.getElementById("r-background");
const counterEl = document.getElementById("background-counter");

/* Status codes that mean "this endpoint isn't there yet", as opposed to
   "your request was wrong" — only these trigger the GitHub fallback. */
const NOT_DEPLOYED = new Set([404, 405, 501]);

/* ---- Domain choices come from data/site.json so they stay in sync ---- */
async function renderDomains() {
  const site = await getSite();
  domainBox.innerHTML = site.domains
    .map(
      (d, i) => `<label class="choice">
        <input type="checkbox" name="domains" value="${esc(d)}" id="r-domain-${i}">
        <span>${esc(d)}</span>
      </label>`
    )
    .join("");
}

function answers() {
  const get = (id) => document.getElementById(id).value;
  return {
    name: get("r-name"),
    affiliation: get("r-affiliation"),
    email: get("r-email"),
    github: get("r-github"),
    position: get("r-position"),
    field_name: get("r-field"),
    background: get("r-background"),
    domains: [...domainBox.querySelectorAll("input:checked")].map((el) => el.value),
  };
}

/* ---- Error display ---- */
function clearErrors() {
  form.querySelectorAll(".field-error").forEach((el) => el.remove());
  form.querySelectorAll(".form-field.is-invalid").forEach((el) => el.classList.remove("is-invalid"));
}

function showErrors(errors) {
  clearErrors();
  let first = null;
  for (const [field, message] of Object.entries(errors)) {
    const wrap = form.querySelector(`[data-field="${field}"]`);
    if (!wrap) continue;
    wrap.classList.add("is-invalid");
    const p = document.createElement("p");
    p.className = "field-error";
    p.textContent = message;
    wrap.appendChild(p);
    first = first ?? wrap;
  }
  if (first) {
    first.scrollIntoView({ behavior: "smooth", block: "center" });
    first.querySelector("input, textarea")?.focus({ preventScroll: true });
  }
}

function setStatus(message, tone = "") {
  statusEl.textContent = message;
  statusEl.className = `submit-status${tone ? ` is-${tone}` : ""}`;
}

function updateCounter() {
  const n = backgroundEl.value.trim().length;
  counterEl.textContent = `${n} / ${LIMITS.background.min} min`;
  counterEl.classList.toggle("is-met", n >= LIMITS.background.min);
}

/* ---- GitHub fallback: the same application as a prefilled issue ---- */
async function openOnGitHub(a) {
  const site = await getSite();
  const repo = String(site.github?.bench_repo || "").replace(/\/$/, "");
  if (!repo) {
    setStatus('The benchmark repository is not configured — use "Copy as Markdown" and email it instead.', "error");
    return;
  }
  const url =
    `${repo}/issues/new?title=${encodeURIComponent(`Reviewer application: ${a.name.trim()}`)}` +
    `&body=${encodeURIComponent(buildMarkdown(a))}`;
  const opened = window.open(url, "_blank", "noopener");
  setStatus(
    opened
      ? 'Your application opened on GitHub — check it over and press "Submit new issue".'
      : 'Allow pop-ups, or use "Copy as Markdown" and open a GitHub issue yourself.',
    opened ? "success" : "error"
  );
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const a = answers();
  const errors = validateApplication(a);
  if (Object.keys(errors).length) {
    showErrors(errors);
    setStatus("Please fix the highlighted fields.", "error");
    return;
  }
  clearErrors();

  submitBtn.disabled = true;
  setStatus("Sending your application…");
  try {
    await controlPlaneFetch(REVIEWER_ENDPOINT, {
      method: "POST",
      body: JSON.stringify(buildReviewerDocument(a)),
    });
    form.reset();
    domainBox.querySelectorAll("input:checked").forEach((el) => (el.checked = false));
    updateCounter();
    setStatus("Thank you — your application was received. We will be in touch by email.", "success");
  } catch (error) {
    if (NOT_DEPLOYED.has(error?.status)) {
      // Expected until the reviewer endpoint ships.
      await openOnGitHub(a);
    } else if (error?.status === 422) {
      setStatus(error.message || "Some answers were rejected. Please check them and try again.", "error");
    } else {
      setStatus(
        `${error?.message || "The application could not be sent."} Opening it on GitHub instead…`,
        "error"
      );
      await openOnGitHub(a);
    }
  } finally {
    submitBtn.disabled = false;
  }
});

/* ---- Copy fallback ---- */
document.getElementById("reviewer-copy").addEventListener("click", async () => {
  const feedback = document.getElementById("reviewer-copy-feedback");
  try {
    await navigator.clipboard.writeText(buildMarkdown(answers()));
    feedback.textContent = "Copied";
  } catch {
    feedback.textContent = "Copy failed — select the text manually.";
  }
  feedback.classList.add("is-visible");
  window.setTimeout(() => feedback.classList.remove("is-visible"), 2400);
});

backgroundEl.addEventListener("input", updateCounter);
updateCounter();
renderDomains().catch((err) => {
  console.error("Reviewer form failed to load domains:", err);
  setStatus("Domain list could not be loaded. Please reload the page.", "error");
});
