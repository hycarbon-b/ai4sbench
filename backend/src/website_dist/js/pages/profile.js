/* The profile uses the same cookie session and public proposal board as the rest of the site. */
import { currentUser, linkGitHub, signInWithGitHub } from "../app.js?v=20261015-1";
import { getTasks } from "../data.js?v=20261012-2";
import { displayStatus } from "../lifecycle.js?v=20261012-2";

const state = document.getElementById("profile-state");
const identity = document.getElementById("profile-identity");
const signIn = document.getElementById("profile-signin");
const proposals = document.getElementById("proposals");
const toReview = document.getElementById("to-review");

function renderList(container, items, emptyText) {
  container.replaceChildren();
  if (!items.length) {
    const empty = document.createElement("p");
    empty.className = "text-muted";
    empty.textContent = emptyText;
    container.append(empty);
    return;
  }
  const list = document.createElement("ul");
  list.className = "profile-proposal-list";
  for (const task of items) {
    const row = document.createElement("li");
    const link = document.createElement("a");
    link.href = `../tasks/task.html?id=${encodeURIComponent(task.task_slug || task.id)}`;
    link.textContent = task.title;
    const status = document.createElement("span");
    status.textContent = displayStatus(task).replaceAll("_", " ");
    row.append(link, status);
    list.append(row);
  }
  container.append(list);
}

async function showUser(user) {
  identity.replaceChildren();
  if (!user) {
    state.hidden = false;
    state.textContent = "Sign in with GitHub to view your profile and proposals.";
    signIn.hidden = false;
    identity.hidden = true;
    proposals.hidden = true;
    toReview.hidden = true;
    return;
  }

  state.hidden = true;
  signIn.hidden = true;
  identity.hidden = false;
  const name = document.createElement("strong");
  name.textContent = `@${user.github_login || user.email}`;
  identity.append(name);
  if (user.can_review) {
    const badge = document.createElement("span");
    badge.className = "site-nav__reviewer-badge";
    badge.textContent = "Reviewer";
    identity.append(badge);
  }
  const email = document.createElement("p");
  email.textContent = user.email;
  identity.append(email);
  if (!user.github_linked) {
    const connect = document.createElement("button");
    connect.type = "button";
    connect.className = "btn btn--secondary btn--sm";
    connect.textContent = "Connect GitHub";
    connect.addEventListener("click", async () => {
      connect.disabled = true;
      try { await linkGitHub(); }
      catch (error) { state.hidden = false; state.textContent = error.message; }
      finally { connect.disabled = false; }
    });
    identity.append(connect);
    if (user.can_review) {
      const note = document.createElement("p");
      note.className = "text-muted";
      note.textContent = "You can save review drafts now. Connect GitHub to publish a review.";
      identity.append(note);
    }
  }
  proposals.hidden = false;
  toReview.hidden = !user.can_review;

  try {
    const tasks = await getTasks();
    const login = String(user.github_login || "").toLowerCase();
    renderList(
      document.getElementById("profile-proposals"),
      tasks.filter((task) => login && String(task.github || "").toLowerCase() === login),
      "You have no published proposals yet.",
    );
    if (user.can_review) {
      renderList(
        document.getElementById("profile-to-review"),
        tasks.filter((task) => displayStatus(task) === "pending" && String(task.github || "").toLowerCase() !== login),
        "No proposals currently need a first review.",
      );
    }
  } catch (error) {
    const message = `Proposals could not be loaded: ${error.message}`;
    renderList(document.getElementById("profile-proposals"), [], message);
    if (user.can_review) renderList(document.getElementById("profile-to-review"), [], message);
  }
}

signIn.addEventListener("click", async () => {
  signIn.disabled = true;
  try { await signInWithGitHub(); }
  catch (error) { state.textContent = error.message; }
  finally { signIn.disabled = false; }
});
document.addEventListener("ai4sbench:authchange", (event) => { void showUser(event.detail); });
currentUser().then(showUser).catch(() => showUser(null));
