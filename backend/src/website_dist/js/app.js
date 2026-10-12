/* ============================================================
   AI4S-Benchmark · App shell
   Global behavior: navigation, GitHub links, footer, a11y bits.
   ============================================================ */

import { getSite, ROOT } from "./data.js?v=20261012-2";
import "./motion.js?v=20261012-2";

const controlPlaneConfig = getSite().then((site) => String(site.control_plane_url || "").replace(/\/$/, ""));

/**
 * Turn a control-plane error body into something a person can act on.
 * FastAPI reports validation failures as a `detail` array of
 * { loc: ["body", "<field>"], msg } entries; collapsing that to
 * "Request failed (422)" hides exactly which field was rejected.
 */
function describeError(body, status) {
  const detail = body?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail.length) {
    const parts = detail.slice(0, 4).map((d) => {
      const field = Array.isArray(d?.loc) ? d.loc[d.loc.length - 1] : null;
      const msg = d?.msg ?? "is invalid";
      return field ? `${field}: ${msg}` : msg;
    });
    const more = detail.length > parts.length ? ` (+${detail.length - parts.length} more)` : "";
    return `${parts.join("; ")}${more}`;
  }
  return `Request failed (${status})`;
}

export async function controlPlaneFetch(path, options = {}) {
  const baseUrl = await controlPlaneConfig;
  if (!baseUrl) throw new Error("Proposal submissions are not configured yet.");
  const response = await fetch(`${baseUrl}${path}`, {
    credentials: "include",
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const error = new Error(describeError(body, response.status));
    // Callers branch on this: a missing endpoint is not a rejected request.
    error.status = response.status;
    throw error;
  }
  return response.status === 204 ? undefined : response.json();
}

export async function currentUser() {
  return controlPlaneFetch("/api/v1/auth/me");
}

export async function signOut() {
  await controlPlaneFetch("/api/v1/auth/logout", { method: "POST" });
  try {
    await currentUser();
  } catch (error) {
    if (error.status === 401) {
      document.dispatchEvent(new CustomEvent("ai4sbench:authchange", { detail: null }));
      return;
    }
    throw error;
  }
  throw new Error("The control-plane session is still active after sign-out.");
}

export async function signInWithGitHub() {
  // Open synchronously while the click gesture is still active. Browsers otherwise
  // treat a popup opened after the asynchronous authorization request as unsolicited.
  const popup = window.open("", "ai4sbench-github", "popup,width=700,height=780");
  if (!popup) throw new Error("Allow popups to continue with GitHub.");
  popup.document.title = "Opening GitHub sign-in…";
  try {
    const baseUrl = await controlPlaneConfig;
    if (!baseUrl) throw new Error("Proposal submissions are not configured yet.");
    popup.location.assign(`${baseUrl}/auth/github/start`);
  } catch (error) {
    popup.close();
    throw error;
  }
  return new Promise((resolve, reject) => {
    const poll = window.setInterval(async () => {
      try {
        const user = await currentUser();
        window.clearInterval(poll);
        popup.close();
        document.dispatchEvent(new CustomEvent("ai4sbench:authchange", { detail: user }));
        resolve(user);
      } catch {
        if (popup.closed) {
          window.clearInterval(poll);
          reject(new Error("GitHub sign-in was cancelled."));
        }
      }
    }, 800);
  });
}

/* ---- Mobile navigation ---- */
const toggle = document.querySelector(".nav-toggle");
const nav = document.querySelector(".site-nav");

if (toggle && nav) {
  toggle.addEventListener("click", () => {
    const open = nav.classList.toggle("is-open");
    toggle.setAttribute("aria-expanded", String(open));
  });
  // Close the drawer when a link is chosen or focus escapes.
  nav.addEventListener("click", (e) => {
    if (e.target.closest("a")) {
      nav.classList.remove("is-open");
      toggle.setAttribute("aria-expanded", "false");
    }
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && nav.classList.contains("is-open")) {
      nav.classList.remove("is-open");
      toggle.setAttribute("aria-expanded", "false");
      toggle.focus();
    }
  });
}

/* ---- Mark the current page in the nav ---- */
const page = document.body.dataset.page;
if (page) {
  document.querySelectorAll(`[data-nav="${page}"]`).forEach((link) => {
    link.setAttribute("aria-current", "page");
  });
}

/* ---- Account: its HTML slot reserves space before the session request. ---- */
const account = document.querySelector("[data-account]");
const accountButton = account?.querySelector("[data-account-toggle]");
const accountMenu = document.createElement("div");
accountMenu.className = "site-nav__account-menu";
accountMenu.id = "account-menu";
accountMenu.hidden = true;
account?.append(accountMenu);
let accountUser = null;
let accountRevision = 0;

function closeAccountMenu() {
  accountMenu.hidden = true;
  accountButton?.setAttribute("aria-expanded", "false");
}

function renderAccount(user, { unavailable = false } = {}) {
  if (!accountButton) return;
  accountUser = user;
  closeAccountMenu();
  accountButton.replaceChildren();
  accountButton.disabled = unavailable;
  if (!user) {
    accountButton.textContent = "Sign in";
    accountButton.setAttribute("aria-label", unavailable ? "GitHub sign-in is unavailable" : "Sign in with GitHub");
    accountMenu.replaceChildren();
    return;
  }

  const login = user.github_login || user.email;
  const avatar = document.createElement("span");
  avatar.className = "site-nav__avatar";
  avatar.textContent = login.slice(0, 1).toUpperCase();
  if (user.github_login) {
    const image = document.createElement("img");
    image.src = `https://github.com/${encodeURIComponent(user.github_login)}.png?size=80`;
    image.alt = "";
    image.width = 34;
    image.height = 34;
    image.addEventListener("error", () => image.remove(), { once: true });
    avatar.append(image);
  }
  accountButton.append(avatar);
  accountButton.setAttribute("aria-label", `Account menu for ${login}`);

  const identity = document.createElement("div");
  identity.className = "site-nav__account-identity";
  const caption = document.createElement("span");
  caption.className = "site-nav__account-caption";
  caption.textContent = "Signed in as";
  const name = document.createElement("strong");
  name.textContent = `@${login}`;
  identity.append(caption, name);
  if (user.can_review) {
    const badge = document.createElement("span");
    badge.className = "site-nav__reviewer-badge";
    badge.textContent = "Reviewer";
    identity.append(badge);
  }
  accountMenu.replaceChildren(identity);
  const links = [
    ["Profile", `${ROOT}profile/`],
    ["Proposals", `${ROOT}profile/#proposals`],
    ...(user.can_review ? [["To review", `${ROOT}profile/#to-review`]] : []),
  ];
  for (const [label, href] of links) {
    const link = document.createElement("a");
    link.href = href;
    link.textContent = label;
    accountMenu.append(link);
  }
  const logout = document.createElement("button");
  logout.type = "button";
  logout.className = "site-nav__signout";
  logout.textContent = "Sign out";
  logout.addEventListener("click", async () => {
    logout.disabled = true;
    try { await signOut(); }
    catch (error) { console.error("GitHub sign-out failed:", error); logout.disabled = false; }
  });
  accountMenu.append(logout);
}

async function refreshAccount() {
  const revision = ++accountRevision;
  try {
    const user = await currentUser();
    if (revision === accountRevision) renderAccount(user);
  }
  catch (error) {
    if (revision === accountRevision) {
      renderAccount(null, { unavailable: error.message === "Proposal submissions are not configured yet." });
    }
  }
}

accountButton?.addEventListener("click", async () => {
  if (accountUser) {
    const open = accountMenu.hidden;
    accountMenu.hidden = !open;
    accountButton.setAttribute("aria-expanded", String(open));
    return;
  }
  accountButton.disabled = true;
  try { await signInWithGitHub(); }
  catch (error) { console.error("GitHub sign-in failed:", error); }
  finally { accountButton.disabled = false; void refreshAccount(); }
});
document.addEventListener("ai4sbench:authchange", (event) => {
  accountRevision += 1;
  renderAccount(event.detail);
});
document.addEventListener("click", (event) => {
  if (account && !account.contains(event.target)) closeAccountMenu();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !accountMenu.hidden) {
    closeAccountMenu();
    accountButton?.focus();
  }
});
void refreshAccount();

/* ---- Wire configured GitHub links (data-gh="key") ---- */
getSite()
  .then((site) => {
    document.querySelectorAll("[data-gh]").forEach((el) => {
      const key = el.dataset.gh;
      const url = site.github?.[key];
      if (url) {
        el.href = url;
      } else {
        el.href = site.github?.org_url ?? "#";
      }
    });
    // Configured links that are not GitHub, e.g. data-link="discord"
    document.querySelectorAll("[data-link]").forEach((el) => {
      const url = site[el.dataset.link];
      if (url) el.href = url;
    });
  })
  .catch((err) => console.error("Site config failed to load:", err));

/* ---- Footer year ---- */
const yearEl = document.querySelector("[data-year]");
if (yearEl) yearEl.textContent = new Date().getFullYear();

export { ROOT };
