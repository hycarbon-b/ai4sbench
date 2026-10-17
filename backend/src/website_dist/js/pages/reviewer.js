/* ============================================================
   AI4S-Benchmark · Reviewer application page

   Applications require a GitHub session. The backend records the
   signed-in identity and, when enabled, checks email verification.
   ============================================================ */

import { getSite } from "../data.js?v=20261012-2";
import { esc } from "../components.js?v=20261012-2";
import { controlPlaneFetch, currentUser, signInWithGitHub } from "../app.js?v=20261015-2";
import {
  LIMITS,
  REVIEWER_ENDPOINT,
  buildMarkdown,
  buildReviewerDocument,
  validateApplication,
} from "../reviewer.js?v=20261018-1";

const form = document.getElementById("reviewer-form");
const statusEl = document.getElementById("reviewer-status");
const submitBtn = document.getElementById("reviewer-submit");
const domainBox = document.getElementById("domain-options");
const backgroundEl = document.getElementById("r-background");
const counterEl = document.getElementById("background-counter");
const emailEl = document.getElementById("r-email");
const githubEl = document.getElementById("r-github");
const authStatusEl = document.getElementById("reviewer-auth-status");
const signInBtn = document.getElementById("reviewer-sign-in");
const otpBox = document.getElementById("reviewer-email-otp");
const otpCodeEl = document.getElementById("r-email-code");
const otpStatusEl = document.getElementById("reviewer-email-otp-status");
const sendCodeBtn = document.getElementById("reviewer-send-code");
const verifyCodeBtn = document.getElementById("reviewer-verify-code");
let otpRequired = null;
let challengeId = null;
let challengeEmail = null;
let verificationToken = null;
let verifiedEmail = null;
let authUser = null;
let authReady = false;
let authError = "";
let githubLoginEnabled = null;
let submitBusy = false;
let codeBusy = false;
submitBtn.disabled = true;
sendCodeBtn.disabled = true;

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

function normalizedEmail() {
  return emailEl.value.trim().toLowerCase();
}

function clearEmailProof() {
  challengeId = null;
  challengeEmail = null;
  verificationToken = null;
  verifiedEmail = null;
  otpCodeEl.value = "";
  verifyCodeBtn.disabled = true;
  otpStatusEl.textContent = "";
}

emailEl.addEventListener("input", clearEmailProof);

function signedIn() {
  return Boolean(authUser?.github_login);
}

function updateSubmissionState() {
  submitBtn.disabled = submitBusy || otpRequired === null || !signedIn();
  sendCodeBtn.disabled = codeBusy || otpRequired !== true || !signedIn();
}

function showAuthState() {
  if (!authReady) {
    authStatusEl.textContent = "Checking your GitHub sign-in…";
  } else if (signedIn()) {
    authStatusEl.textContent = `Signed in as @${authUser.github_login}.`;
  } else {
    authStatusEl.textContent = authError || (githubLoginEnabled === false
      ? "GitHub sign-in is unavailable right now."
      : "Sign in with GitHub before sending your reviewer application.");
  }
  signInBtn.hidden = !authReady || signedIn() || githubLoginEnabled !== true;
  updateSubmissionState();
}

function applyUser(user) {
  if (authUser?.id !== user?.id) clearEmailProof();
  authUser = user?.github_login ? user : null;
  authReady = true;
  authError = user && !user.github_login ? "A GitHub account is required to apply." : "";
  githubEl.value = authUser?.github_login || "";
  showAuthState();
}

async function loadUser() {
  try {
    applyUser(await currentUser());
  } catch (error) {
    applyUser(null);
    if (error?.status !== 401) {
      authError = "Sign-in status could not be checked. Please reload the page.";
      showAuthState();
    }
  }
}

signInBtn.addEventListener("click", async () => {
  signInBtn.disabled = true;
  authStatusEl.textContent = "Opening GitHub sign-in…";
  try {
    applyUser(await signInWithGitHub());
  } catch (error) {
    authError = error.message || "GitHub sign-in did not complete.";
    showAuthState();
  } finally {
    signInBtn.disabled = false;
  }
});

document.addEventListener("ai4sbench:authchange", (event) => applyUser(event.detail ?? null));
loadUser();

async function loadOtpRequirement() {
  try {
    const config = await controlPlaneFetch("/api/v1/auth/config");
    if (typeof config?.form_mail_require_otp !== "boolean") {
      throw new Error("Email verification settings are unavailable. Please reload the page.");
    }
    otpRequired = config.form_mail_require_otp;
    githubLoginEnabled = config.github_login_enabled === true;
    otpBox.hidden = !otpRequired;
    showAuthState();
  } catch (error) {
    setStatus(error.message || "The form is unavailable. Please reload the page.", "error");
  }
}

sendCodeBtn.addEventListener("click", async () => {
  const email = normalizedEmail();
  if (!signedIn()) {
    otpStatusEl.textContent = "Sign in with GitHub before requesting a code.";
    return;
  }
  if (!email || !emailEl.checkValidity()) {
    showErrors({ email: "Enter a valid email address first." });
    return;
  }
  clearErrors();
  clearEmailProof();
  const userId = authUser.id;
  codeBusy = true;
  updateSubmissionState();
  otpStatusEl.textContent = "Sending code…";
  try {
    const result = await controlPlaneFetch("/api/v1/reviewers/email-otp", {
      method: "POST",
      body: JSON.stringify({ email }),
    });
    if (authUser?.id !== userId || normalizedEmail() !== email) return;
    challengeId = result.challenge_id;
    challengeEmail = email;
    otpStatusEl.textContent = "Code sent. Check your inbox and enter it here.";
    verifyCodeBtn.disabled = false;
    otpCodeEl.focus();
  } catch (error) {
    if (error?.status === 401) applyUser(null);
    otpStatusEl.textContent = error.message || "Could not send the code. Please try again.";
  } finally {
    codeBusy = false;
    updateSubmissionState();
  }
});

verifyCodeBtn.addEventListener("click", async () => {
  if (!signedIn()) {
    otpStatusEl.textContent = "Sign in with GitHub before verifying your email.";
    return;
  }
  const email = normalizedEmail();
  if (!challengeId || challengeEmail !== email || !/^\d{6}$/.test(otpCodeEl.value.trim())) {
    otpStatusEl.textContent = "Enter the six-digit code sent to this email address.";
    return;
  }
  verifyCodeBtn.disabled = true;
  const userId = authUser.id;
  otpStatusEl.textContent = "Verifying…";
  try {
    const result = await controlPlaneFetch("/api/v1/reviewers/email-otp/verify", {
      method: "POST",
      body: JSON.stringify({ email, challenge_id: challengeId, code: otpCodeEl.value.trim() }),
    });
    if (authUser?.id !== userId || normalizedEmail() !== email) return;
    verificationToken = result.verification_token;
    verifiedEmail = email;
    otpStatusEl.textContent = "Email verified. You can send your application now.";
  } catch (error) {
    if (error?.status === 401) applyUser(null);
    otpStatusEl.textContent = error.message || "The code could not be verified.";
    verifyCodeBtn.disabled = !signedIn();
  }
});

loadOtpRequirement();

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!signedIn()) {
    setStatus(githubLoginEnabled === false
      ? "GitHub sign-in is unavailable right now. Please try again later."
      : "Sign in with GitHub before sending your reviewer application.", "error");
    if (!signInBtn.hidden) signInBtn.focus();
    return;
  }
  const a = answers();
  const errors = validateApplication(a);
  if (Object.keys(errors).length) {
    showErrors(errors);
    setStatus("Please fix the highlighted fields.", "error");
    return;
  }
  clearErrors();

  if (otpRequired === null) {
    setStatus("Email verification settings are still loading. Please try again.", "error");
    return;
  }
  if (otpRequired && (!verificationToken || verifiedEmail !== normalizedEmail())) {
    showErrors({ email_verification: "Verify your email before sending the application." });
    setStatus("Email verification is required.", "error");
    return;
  }

  submitBusy = true;
  updateSubmissionState();
  setStatus("Sending your application…");
  try {
    const document = buildReviewerDocument(a);
    if (otpRequired) document.email_verification_token = verificationToken;
    await controlPlaneFetch(REVIEWER_ENDPOINT, {
      method: "POST",
      body: JSON.stringify(document),
    });
    form.reset();
    githubEl.value = authUser.github_login;
    clearEmailProof();
    domainBox.querySelectorAll("input:checked").forEach((el) => (el.checked = false));
    updateCounter();
    setStatus("Thank you. Your application was received, and we will be in touch by email.", "success");
  } catch (error) {
    if (error?.status === 401) {
      applyUser(null);
      setStatus("Your session expired. Sign in with GitHub, then send the application again.", "error");
    } else if (error?.status === 422) {
      setStatus(error.message || "Some answers were rejected. Please check them and try again.", "error");
    } else {
      setStatus(error?.message || "The application could not be sent. Please try again.", "error");
    }
  } finally {
    submitBusy = false;
    updateSubmissionState();
  }
});

/* ---- Copy fallback ---- */
document.getElementById("reviewer-copy").addEventListener("click", async () => {
  const feedback = document.getElementById("reviewer-copy-feedback");
  try {
    await navigator.clipboard.writeText(buildMarkdown(answers()));
    feedback.textContent = "Copied";
  } catch {
    feedback.textContent = "Copy failed. Select the text manually.";
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
