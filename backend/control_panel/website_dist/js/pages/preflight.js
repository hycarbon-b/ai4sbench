/* ============================================================
   AI4S-Benchmark · Task PR pre-flight check page
   Gathers a task's files — from a folder on the contributor's
   computer (read in the browser, never uploaded) or from a public
   GitHub PR / folder URL (GitHub's public API) — and runs the
   rules in ../task-check.js on each task found.
   ============================================================ */

import { getTasks } from "../data.js?v=20260921-3";
import { esc } from "../components.js?v=20260921-3";
import { displayStatus, isApproved, STATUS_INFO } from "../lifecycle.js?v=20260921-3";
import { checkTask, findTaskRoots, changedTaskRoots, repoTaskPath, REQUIRED_FILES } from "../task-check.js?v=20260921-3";

const MAX_BYTES = 512 * 1024; // required files are small; anything bigger is not read
const MAX_TASKS = 12;
const API = "https://api.github.com";
const RAW = "https://raw.githubusercontent.com";

const els = {
  folder: document.getElementById("pf-folder"),
  url: document.getElementById("pf-url"),
  urlForm: document.getElementById("pf-url-form"),
  status: document.getElementById("pf-status"),
  result: document.getElementById("pf-result"),
};

const ICON = {
  ok: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="8" cy="8" r="6.4"/><path d="m5.3 8.2 1.8 1.9 3.6-3.9"/></svg>',
  fail: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><circle cx="8" cy="8" r="6.4"/><path d="m5.7 5.7 4.6 4.6m0-4.6-4.6 4.6"/></svg>',
  warn: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M8 1.8 15 14H1z"/><path d="M8 6.2v3.6M8 11.9v.1"/></svg>',
  tip: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" aria-hidden="true"><path d="M5.6 11.2c-1.3-1-2.1-2.4-2.1-4A4.5 4.5 0 0 1 8 2.7a4.5 4.5 0 0 1 4.5 4.5c0 1.6-.8 3-2.1 4M6 13.2h4M6.6 15h2.8"/></svg>',
  info: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true"><circle cx="8" cy="8" r="6.4"/><path d="M8 7.2v3.6M8 5v.1" stroke-linecap="round"/></svg>',
};
const VERDICT = {
  pass: "Ready for the PR check",
  warn: "Passes the PR check · review the warnings",
  fail: "Would fail the PR check",
};

function setStatus(message, error = false) {
  els.status.textContent = message;
  els.status.classList.toggle("is-error", error);
}

/* ---- Rendering ---------------------------------------------- */

function reportHTML({ name, path, report }) {
  return `<article class="preflight-task">
    <div class="preflight-task__head">
      <div>
        <h3>${esc(name)}</h3>
        ${path ? `<div class="preflight-task__path">${esc(path)}</div>` : ""}
      </div>
      <span class="preflight-verdict is-${report.verdict}">${VERDICT[report.verdict]}</span>
    </div>
    ${report.groups
      .map(
        (g) => `<section class="preflight-group">
          <h4>${esc(g.title)}</h4>
          <div class="checklist"><ul>${g.items
            .map((i) => `<li class="is-${i.level}">${ICON[i.level]}<span>${i.message}</span></li>`)
            .join("")}</ul></div>
        </section>`
      )
      .join("")}
  </article>`;
}

function renderReports(sourceHTML, reports, note = "") {
  els.result.innerHTML = `<p class="preflight-status">${sourceHTML}</p>${note ? `<div class="notice guide-callout"><p>${note}</p></div>` : ""}${reports.map(reportHTML).join("")}`;
  els.result.scrollIntoView({ behavior: "smooth", block: "start" });
}

/* ---- Proposal status from the public task board ---------------- */

async function proposalResolver() {
  const board = await getTasks().catch(() => []);
  return {
    resolve(link) {
      const task = board.find((t) => t.discussion_number === link.number);
      if (!task) return null;
      return { approved: isApproved(task), statusLabel: STATUS_INFO[displayStatus(task)].label, task };
    },
  };
}

/* ---- Local folder --------------------------------------------- */

async function checkFolder(fileList) {
  const files = [...fileList];
  if (!files.length) return;
  const paths = files.map((f) => f.webkitRelativePath || f.name);
  const roots = findTaskRoots(paths);
  if (!roots.length) {
    setStatus("No task.toml was found in that folder. Choose the task folder itself (the one that contains task.toml), or a folder that contains it.", true);
    els.result.innerHTML = "";
    return;
  }
  setStatus(`Reading ${Math.min(roots.length, MAX_TASKS)} task ${roots.length === 1 ? "folder" : "folders"} on this computer…`);
  const reports = [];
  for (const root of roots.slice(0, MAX_TASKS)) {
    const map = new Map();
    for (const file of files) {
      const p = file.webkitRelativePath || file.name;
      if (!p.startsWith(`${root}/`)) continue;
      const rel = p.slice(root.length + 1);
      if (!REQUIRED_FILES.includes(rel)) continue;
      map.set(rel, file.size <= MAX_BYTES ? await file.text() : null);
    }
    const repoPath = repoTaskPath(root);
    reports.push({ name: root.split("/").pop(), path: repoPath ?? root, report: checkTask({ files: map, repoPath }) });
  }
  const more = roots.length > MAX_TASKS ? ` Showing the first ${MAX_TASKS} of ${roots.length}.` : "";
  setStatus("");
  renderReports(
    `Checked ${reports.length} ${reports.length === 1 ? "task" : "tasks"} from your computer. Nothing was uploaded.${more}`,
    reports
  );
}

els.folder.addEventListener("change", () => {
  checkFolder(els.folder.files).catch((error) => {
    console.error("Pre-flight folder check failed:", error);
    setStatus("The folder could not be read. Try choosing it again.", true);
  });
});

/* ---- GitHub ----------------------------------------------------- */

class GitHubError extends Error {}

async function gh(path) {
  const response = await fetch(`${API}${path}`, { headers: { Accept: "application/vnd.github+json" } });
  if (response.status === 403 || response.status === 429) {
    const reset = Number(response.headers.get("x-ratelimit-reset"));
    const when = reset ? new Date(reset * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "later";
    throw new GitHubError(
      `GitHub's public API allows 60 requests an hour per network, and that allowance is used up. Try again after ${when}, or check a folder on your computer instead.`
    );
  }
  if (response.status === 404) throw new GitHubError("GitHub could not find that. Check the link, and that the repository is public.");
  if (!response.ok) throw new GitHubError(`GitHub answered ${response.status}. Try again in a moment.`);
  return response.json();
}

async function raw(repo, ref, path) {
  const response = await fetch(`${RAW}/${repo}/${ref}/${path.split("/").map(encodeURIComponent).join("/")}`);
  return response.ok ? response.text() : null;
}

function parseGitHubURL(input) {
  let url;
  try {
    url = new URL(String(input).trim());
  } catch {
    return null;
  }
  if (!/^(www\.)?github\.com$/i.test(url.hostname)) return null;
  const segs = url.pathname.split("/").filter(Boolean).map(decodeURIComponent);
  if (segs.length < 2) return null;
  const [owner, repo, kind, ...rest] = segs;
  const name = repo.replace(/\.git$/, "");
  if (kind === "pull" && /^\d+$/.test(rest[0] ?? "")) return { type: "pr", owner, repo: name, number: Number(rest[0]) };
  if (kind === "tree" && rest.length) return { type: "tree", owner, repo: name, segs: rest };
  if (!kind) return { type: "repo", owner, repo: name };
  return null;
}

/** Recursive tree for a commit: path → { size }. Null when GitHub truncates it. */
async function treeOf(repo, ref) {
  const tree = await gh(`/repos/${repo}/git/trees/${encodeURIComponent(ref)}?recursive=1`);
  if (tree.truncated) return null;
  return new Map(tree.tree.filter((e) => e.type === "blob").map((e) => [e.path, { size: e.size }]));
}

/** Listing for just one task directory, for repositories too large for one tree call. */
async function listTask(repo, ref, root) {
  const out = new Map();
  const walk = async (dir) => {
    const entries = await gh(`/repos/${repo}/contents/${dir.split("/").map(encodeURIComponent).join("/")}?ref=${encodeURIComponent(ref)}`);
    for (const e of entries) {
      if (e.type === "file") out.set(e.path, { size: e.size });
      else if (e.type === "dir" && ["environment", "solution", "tests"].includes(e.name) && dir === root) await walk(e.path);
    }
  };
  await walk(root);
  return out;
}

async function filesFor(repo, ref, root, listing) {
  const entries = listing ?? (await listTask(repo, ref, root));
  const map = new Map();
  await Promise.all(
    REQUIRED_FILES.map(async (rel) => {
      const entry = entries.get(`${root}/${rel}`);
      if (!entry) return;
      map.set(rel, entry.size <= MAX_BYTES ? await raw(repo, ref, `${root}/${rel}`) : null);
    })
  );
  return map;
}

async function checkPullRequest({ owner, repo, number }) {
  setStatus(`Reading PR #${number} from GitHub…`);
  const pr = await gh(`/repos/${owner}/${repo}/pulls/${number}`);
  const changed = [];
  for (let page = 1; page <= 3; page++) {
    const batch = await gh(`/repos/${owner}/${repo}/pulls/${number}/files?per_page=100&page=${page}`);
    changed.push(...batch.map((f) => f.filename));
    if (batch.length < 100) break;
  }
  const roots = changedTaskRoots(changed);
  const head = pr.head?.repo?.full_name;
  const sha = pr.head?.sha;
  const source = `<a href="${esc(pr.html_url)}" target="_blank" rel="noopener">PR #${number}</a> in ${esc(`${owner}/${repo}`)} · “${esc(pr.title)}” · ${pr.state === "open" ? "open" : esc(pr.merged_at ? "merged" : "closed")}`;
  if (!roots.length) {
    renderReports(source, [], "This PR does not change any task directory under <code>tasks/&lt;domain&gt;/&lt;field&gt;/&lt;task-slug&gt;/</code>, so the task checks do not apply to it.");
    setStatus("");
    return;
  }
  if (!head || !sha) throw new GitHubError("The branch behind this PR is no longer available on GitHub.");
  const listing = await treeOf(head, sha).catch(() => null);
  const proposal = await proposalResolver();
  const reports = [];
  for (const root of roots.slice(0, MAX_TASKS)) {
    const files = await filesFor(head, sha, root, listing);
    reports.push({ name: root.split("/").pop(), path: root, report: checkTask({ files, repoPath: root, pr: { body: pr.body ?? "" }, proposal }) });
  }
  setStatus("");
  renderReports(source, reports);
}

async function checkTree({ owner, repo, segs }) {
  const full = `${owner}/${repo}`;
  setStatus(`Reading ${full} from GitHub…`);
  // A branch name can contain "/", so try the shortest ref that exists.
  let ref = null;
  let listing = null;
  let base = "";
  for (let n = 1; n <= Math.min(segs.length, 3) && !ref; n++) {
    const candidate = segs.slice(0, n).join("/");
    try {
      listing = await treeOf(full, candidate);
      ref = candidate;
      base = segs.slice(n).join("/");
    } catch (error) {
      if (!(error instanceof GitHubError) || !/could not find/.test(error.message)) throw error;
    }
  }
  if (!ref) throw new GitHubError("GitHub could not find that branch or folder. Check the link, and that the repository is public.");
  return checkListing(full, ref, base, listing, `<a href="https://github.com/${esc(full)}/tree/${esc(ref)}/${esc(base)}" target="_blank" rel="noopener">${esc(full)} · ${esc(ref)}${base ? ` · ${esc(base)}` : ""}</a>`);
}

async function checkRepo({ owner, repo }) {
  const full = `${owner}/${repo}`;
  setStatus(`Reading ${full} from GitHub…`);
  const info = await gh(`/repos/${full}`);
  const ref = info.default_branch;
  const listing = await treeOf(full, ref);
  return checkListing(full, ref, "tasks", listing, `<a href="${esc(info.html_url)}" target="_blank" rel="noopener">${esc(full)}</a> · ${esc(ref)}`);
}

async function checkListing(full, ref, base, listing, sourceHTML) {
  if (!listing) {
    throw new GitHubError("That repository is too large to scan in one go. Link the task folder itself (…/tree/<branch>/tasks/<domain>/<field>/<task-slug>) instead.");
  }
  const prefix = base ? `${base.replace(/\/$/, "")}/` : "";
  const roots = findTaskRoots([...listing.keys()].filter((p) => p.startsWith(prefix) || !base));
  if (!roots.length) {
    setStatus("No task.toml was found at that location. Link a task folder or a folder that contains tasks.", true);
    els.result.innerHTML = "";
    return;
  }
  const reports = [];
  for (const root of roots.slice(0, MAX_TASKS)) {
    const files = await filesFor(full, ref, root, listing);
    reports.push({ name: root.split("/").pop(), path: root, report: checkTask({ files, repoPath: repoTaskPath(root) ?? root }) });
  }
  const note = roots.length > MAX_TASKS ? `Showing the first ${MAX_TASKS} of ${roots.length} tasks found. Link a single task folder to check a specific one.` : "";
  setStatus("");
  renderReports(`${sourceHTML} · ${roots.length} ${roots.length === 1 ? "task" : "tasks"} found`, reports, note);
}

async function checkURL(value) {
  const target = parseGitHubURL(value);
  if (!target) {
    setStatus("Paste a GitHub pull request link (…/pull/123), a folder link (…/tree/<branch>/…) or a repository link.", true);
    return;
  }
  els.result.innerHTML = "";
  try {
    if (target.type === "pr") await checkPullRequest(target);
    else if (target.type === "tree") await checkTree(target);
    else await checkRepo(target);
  } catch (error) {
    console.error("Pre-flight GitHub check failed:", error);
    setStatus(error instanceof GitHubError ? error.message : "GitHub could not be reached. Check your connection and try again.", true);
  }
}

els.urlForm.addEventListener("submit", (event) => {
  event.preventDefault();
  void checkURL(els.url.value);
});
document.querySelectorAll("[data-example]").forEach((btn) =>
  btn.addEventListener("click", () => {
    els.url.value = btn.dataset.example;
    void checkURL(btn.dataset.example);
  })
);

// Deep link: /guide/check.html?url=<github link> runs straight away.
const preset = new URLSearchParams(location.search).get("url");
if (preset) {
  els.url.value = preset;
  void checkURL(preset);
}
