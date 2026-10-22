/* A single account check shared by every Website page after sign-in. */
let dialog;
let active = false;

export function accountCheckStep(user) {
  if (!user) return null;
  if (!user.github_linked) return "github";
  return user.github_email_review_pending ? "email" : null;
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

function accountDialog() {
  if (dialog) return dialog;
  dialog = element("dialog", "account-check");
  dialog.setAttribute("aria-labelledby", "account-check-title");
  document.body.append(dialog);
  return dialog;
}

function dismissedKey(user, step) {
  return `ai4sbench:account-check:${user.id}:${step}`;
}

/** Opens after a new sign-in or invitation redemption; never blocks an invited account. */
export async function reviewAccount(user, { fetchAccount, connectGitHub, reloadUser }, force = false) {
  const step = accountCheckStep(user);
  if (!step || active || (!force && sessionStorage.getItem(dismissedKey(user, step)))) return;
  active = true;
  const modal = accountDialog();
  let currentUser = user;
  let currentStep = step;
  let completed = false;

  function render() {
    modal.replaceChildren();
    const panel = element("div", "account-check__panel");
    const eyebrow = element("p", "account-check__eyebrow", "AI4S-Benchmark · Account check");
    const title = element("h2", "account-check__title", currentStep === "github" ? "Connect GitHub" : "Confirm your email");
    title.id = "account-check-title";
    const intro = element("p", "account-check__intro",
      currentStep === "github"
        ? "Connect GitHub to publish scientific reviews under your own identity. You can continue using your account without it."
        : "Which email should we use for your AI4S-Bench account? Your GitHub email is filled in below.");
    const status = element("p", "account-check__status");
    status.setAttribute("role", "status");
    const actions = element("div", "account-check__actions");
    const later = element("button", "btn btn--secondary", "Maybe later");
    later.type = "button";
    later.addEventListener("click", () => modal.close());
    panel.append(eyebrow, title, intro);

    if (currentStep === "github") {
      const connect = element("button", "btn btn--primary", "Connect GitHub");
      connect.type = "button";
      connect.addEventListener("click", async () => {
        connect.disabled = true;
        later.disabled = true;
        status.textContent = "Opening GitHub…";
        try {
          currentUser = await connectGitHub();
          currentStep = accountCheckStep(currentUser);
          if (currentStep === "email") render();
          else { completed = true; modal.close(); }
        } catch (error) {
          status.textContent = error.message;
          connect.disabled = false;
          later.disabled = false;
        }
      });
      actions.append(later, connect);
    } else {
      const form = element("form", "account-check__form");
      const label = element("label", "account-check__label", "Account email");
      label.htmlFor = "account-check-email";
      const input = element("input", "account-check__input");
      input.id = "account-check-email";
      input.type = "email";
      input.required = true;
      input.maxLength = 200;
      input.autocomplete = "email";
      input.value = currentUser.github_email || currentUser.email;
      const existing = element("p", "account-check__current", `Current email: ${currentUser.email}`);
      const codeLabel = element("label", "account-check__label", "Verification code");
      codeLabel.htmlFor = "account-check-code";
      codeLabel.hidden = true;
      const code = element("input", "account-check__input");
      code.id = "account-check-code";
      code.inputMode = "numeric";
      code.pattern = "[0-9]{6}";
      code.maxLength = 6;
      code.autocomplete = "one-time-code";
      code.hidden = true;
      const resend = element("button", "account-check__resend", "Send a new code");
      resend.type = "button";
      resend.hidden = true;
      resend.addEventListener("click", async () => {
        resend.disabled = true;
        try {
          challenge = await fetchAccount("/api/v1/auth/email/code", {
            method: "POST", body: JSON.stringify({ email: input.value.trim().toLowerCase() }),
          });
          code.value = "";
          status.textContent = "We sent a new code. It expires in 10 minutes.";
        } catch (error) {
          status.textContent = error.message;
        } finally {
          resend.disabled = false;
        }
      });
      const save = element("button", "btn btn--primary", "Use this email");
      save.type = "submit";
      let challenge = null;
      input.addEventListener("input", () => {
        challenge = null;
        code.value = "";
        code.required = false;
        code.hidden = true;
        codeLabel.hidden = true;
        resend.hidden = true;
        save.textContent = "Use this email";
        status.textContent = "";
      });
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const email = input.value.trim().toLowerCase();
        const same = email === currentUser.email.toLowerCase();
        const github = email === String(currentUser.github_email || "").toLowerCase();
        save.disabled = true;
        later.disabled = true;
        try {
          if (!same && !github && !challenge) {
            challenge = await fetchAccount("/api/v1/auth/email/code", {
              method: "POST", body: JSON.stringify({ email }),
            });
            code.hidden = false;
            codeLabel.hidden = false;
            code.required = true;
            resend.hidden = false;
            code.focus();
            save.textContent = "Verify and save";
            status.textContent = "We sent a six-digit code to that address. It expires in 10 minutes.";
            return;
          }
          let verification_token;
          if (challenge) {
            const proof = await fetchAccount("/api/v1/auth/email/code/verify", {
              method: "POST", body: JSON.stringify({ email, challenge_id: challenge.challenge_id, code: code.value }),
            });
            verification_token = proof.verification_token;
          }
          await fetchAccount("/api/v1/auth/email/choice", {
            method: "POST", body: JSON.stringify({ choice: same ? "keep" : "change", email, verification_token }),
          });
          completed = true;
          modal.close();
          await reloadUser();
        } catch (error) {
          status.textContent = error.message;
        } finally {
          save.disabled = false;
          later.disabled = false;
        }
      });
      form.append(label, input, existing, codeLabel, code, resend, status);
      actions.append(later, save);
      form.append(actions);
      panel.append(form);
    }
    if (currentStep === "github") panel.append(status, actions);
    modal.append(panel);
  }

  render();
  modal.showModal();
  modal.addEventListener("close", () => {
    if (!completed && currentStep) sessionStorage.setItem(dismissedKey(currentUser, currentStep), "1");
    active = false;
  }, { once: true });
}
