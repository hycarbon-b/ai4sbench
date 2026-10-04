/* ============================================================
   AI4S-Benchmark · On-site proposal editor
   The supported way for an author to revise a proposal: on the
   task page, with a live rendered preview (Markdown + LaTeX).
   Editing the GitHub Discussion by hand is only a fallback for
   when the control plane is not exposing the update route.

   Permissions: the editor is offered only when the signed-in
   GitHub login matches the proposal's `github` field, and the
   control plane must enforce the same rule server-side — the
   frontend check is a convenience, never the security boundary.

   Backend contract (see README → "On-site editing"):
     PUT /api/v1/proposals/{proposal_id}   body: ProposalSubmission
     → 200 ProposalUpdatedResponse — the author's own GitHub token
       updates the Discussion first; local fields are committed only
       after GitHub accepts, so the page and the Discussion cannot
       drift apart.
     → 401 the GitHub authorization expired  → 403 not the author
     → 404 proposal missing/deleted          → 409 no Discussion identity
     → 502 GitHub refused the update         → 422 field validation
   If the route is absent entirely the editor is never offered and
   authors keep the "Edit on GitHub" link instead.
   ============================================================ */

import { controlPlaneFetch } from "../app.js?v=20260921-3";
import { esc } from "../components.js?v=20260921-3";
import {
  LIMITS,
  FIELD_LABELS,
  DOMAIN_SEPARATOR,
  validateAnswers,
  buildProposalSubmission,
  buildMarkdown,
} from "../proposal.js?v=20260921-3";
import { renderRich, mountMath } from "../richtext.js?v=20260921-3";
import { mountContributorRows } from "../contributor-fields.js?v=20260921-3";
import { splitContributors } from "../people.js?v=20260921-3";
import { getSite, getTask, invalidateTasks } from "../data.js?v=20260921-3";
import { mountFieldAdvice } from "../proposal-advice.js?v=20260921-3";

/* ---- Feature detection --------------------------------------
   The control plane publishes its OpenAPI document. The editor is
   offered only when that document exposes a full-replacement route,
   and we send the verb the document actually advertises rather than
   assuming one: the route shipped as PUT, and a control plane that
   later moves to PATCH keeps working without a site deploy. Sending
   the wrong verb answers 405, which reads to an author as a Save
   button that silently never works. */
const EDIT_ROUTE = "/api/v1/proposals/{proposal_id}";
let methodProbe = null;

/** Resolve the update verb the control plane advertises, or null. */
export function editMethod() {
  if (methodProbe) return methodProbe;
  methodProbe = (async () => {
    try {
      const site = await getSite();
      const baseUrl = String(site.control_plane_url ?? "").replace(/\/$/, "");
      if (!baseUrl) return null;
      const response = await fetch(`${baseUrl}/openapi.json`, { credentials: "omit" });
      if (!response.ok) return null;
      const spec = await response.json();
      const route = spec?.paths?.[EDIT_ROUTE];
      if (!route) return null;
      // Prefer PUT: the published contract is a full replacement, which is
      // exactly what this editor submits.
      if (route.put) return "PUT";
      if (route.patch) return "PATCH";
      return null;
    } catch {
      return null;
    }
  })();
  return methodProbe;
}

export async function editingAvailable() {
  return Boolean(await editMethod());
}

/**
 * Turn a control-plane failure into something the author can act on.
 * Every status in the published contract gets its own sentence; 422
 * already arrives field-by-field from describeError() in app.js, so
 * that message is passed through untouched.
 */
export function saveErrorMessage(error) {
  switch (error?.status) {
    case 401:
      return "Your GitHub authorization expired. Sign in again, then save — your text is still here.";
    case 403:
      return "Only the author of this proposal can edit it.";
    case 404:
      return "This proposal could not be found. It may have been withdrawn.";
    case 409:
      return "This proposal has no linked GitHub Discussion, so it cannot be updated here yet. Please contact the maintainers.";
    case 422:
      return error.message || "Some fields were rejected. Check the highlighted fields and try again.";
    case 502:
      return "GitHub refused the update, so nothing was saved. This is usually temporary — try again shortly.";
    case 405:
    case 501:
      return "Editing on the site is not enabled on the control plane yet.";
    default:
      return error?.message || "The changes could not be saved. Please try again.";
  }
}

/** True when the signed-in user is the proposal's author. */
export function canEdit(task, user) {
  const login = String(user?.github_login ?? "").trim().toLowerCase();
  const owner = String(task?.github ?? "").trim().toLowerCase();
  return Boolean(login && owner && login === owner);
}

const TEXT_FIELDS = [
  ["problem", 8, "What scientific problem does this task address? Why is it important?"],
  ["solvability", 4, "Is this problem solvable in principle? Does its difficulty come from the science itself?"],
  ["references", 4, "Papers, datasets, code or protocols this task builds on."],
  ["software", 3, "The tools, software and dependencies this task requires."],
  ["dataset", 4, "What data is provided, its provenance and license, and what must stay hidden from the agent."],
  ["compute", 2, "Limit for now: one GPU and 16 CPU cores per task."],
  ["workflow", 4, "Leave empty for open questions where no workflow can be prescribed."],
  ["evaluation", 5, "What is the metric, and how is it used to validate the agent's output?"],
  ["leakage", 3, "Could an agent find the answer in public resources? Is the evaluation repeatable?"],
];

function field(key, label, control, hint = "") {
  return `<div class="form-field" data-field="${key}">
    <label for="edit-${key}">${esc(label)}${LIMITS[key]?.min === 0 ? ' <span class="optional">(optional)</span>' : ""}</label>
    ${control}
    ${hint ? `<p class="hint">${esc(hint)}</p>` : ""}
  </div>`;
}

function textarea(key, rows, value, placeholder) {
  return `<textarea id="edit-${key}" name="${key}" rows="${rows}" placeholder="${esc(placeholder)}">${esc(value)}</textarea>`;
}

function editorHTML(task) {
  return `<section class="proposal-editor proposal-editor--wide" id="proposal-editor" aria-labelledby="proposal-editor-h">
    <div class="proposal-editor__head">
      <div>
        <span class="eyebrow">Author workspace</span>
        <h2 id="proposal-editor-h">Edit proposal</h2>
        <p>This is the place to revise your proposal. Saving updates the task page and your GitHub Discussion together, so reviewers always read the same version you see here. Markdown and LaTeX render in the preview exactly as they will on the page.</p>
      </div>
      <button type="button" class="btn btn--secondary" data-edit-cancel>Back to the proposal</button>
    </div>
    <form id="proposal-edit-form" novalidate>
      <div class="proposal-editor__grid">
      <div class="proposal-editor__form">
        ${field("title", FIELD_LABELS.title, `<input type="text" id="edit-title" name="title" maxlength="160" value="${esc(task.title)}">`)}
        <div class="form-field" data-field="domain">
          <label id="edit-domain-label">Domains involved</label>
          <div class="choice-grid" id="edit-domain" role="group" aria-labelledby="edit-domain-label"></div>
        </div>
        ${field("field_name", FIELD_LABELS.field_name, `<input type="text" id="edit-field_name" name="field_name" maxlength="160" value="${esc(task.field_name)}">`)}
        ${TEXT_FIELDS.map(([key, rows, ph]) => field(key, FIELD_LABELS[key], textarea(key, rows, task[key] ?? "", ph))).join("")}
        <div class="form-field" data-field="name" id="edit-contributors"></div>
        <div class="form-field" data-field="affiliation" hidden></div>
        <div class="form-field" data-field="github">
          <label for="edit-github">GitHub username</label>
          <input type="text" id="edit-github" name="github" value="${esc(task.github)}" readonly>
          <p class="hint">The contact account cannot be changed here.</p>
        </div>
      </div>
      <aside class="proposal-editor__preview" aria-label="Live preview">
        <div class="proposal-editor__preview-head">
          <span class="mono-label">Live preview</span>
          <div class="preview-toggle" role="group" aria-label="Preview format">
            <button type="button" class="preview-toggle__btn is-active" data-view="rendered" aria-pressed="true">Rendered</button>
            <button type="button" class="preview-toggle__btn" data-view="markdown" aria-pressed="false">Markdown</button>
          </div>
        </div>
        <div class="proposal-rendered rich" id="edit-rendered"></div>
        <div class="proposal-preview" id="edit-markdown" tabindex="0" hidden></div>
      </aside>
      </div>
      <!-- Outside the grid on purpose: the preview is sticky, and a sticky grid
           item is contained by the whole grid, so while the actions lived inside
           it the preview slid down over them and intercepted clicks on Save. -->
      <div class="proposal-editor__actions">
        <p class="submit-status" id="edit-status" role="status" aria-live="polite"></p>
        <button type="button" class="btn btn--ghost" data-edit-cancel>Cancel</button>
        <button type="submit" class="btn btn--primary" id="edit-save">Save changes</button>
      </div>
    </form>
  </section>`;
}

/**
 * Mount the editor into `root`. Calls onSaved(result) after a successful
 * save and onCancel() when the author leaves without saving.
 */
export function mountEditor({ task, user, root, onSaved, onCancel }) {
  if (!canEdit(task, user)) {
    root.innerHTML = "";
    return;
  }
  root.innerHTML = editorHTML(task);
  const form = root.querySelector("#proposal-edit-form");
  const status = root.querySelector("#edit-status");
  const saveBtn = root.querySelector("#edit-save");
  const rendered = root.querySelector("#edit-rendered");
  const markdown = root.querySelector("#edit-markdown");

  const contributors = mountContributorRows(
    root.querySelector("#edit-contributors"),
    splitContributors(task.name, task.affiliation),
    { onChange: () => schedulePreview() }
  );

  /* ---- Domains: shared list from the control plane, current ones checked ---- */
  const current = new Set(String(task.domain ?? "").split(DOMAIN_SEPARATOR).map((d) => d.trim()).filter(Boolean));
  const domainBox = root.querySelector("#edit-domain");
  const renderDomains = (items) => {
    const all = [...new Set([...items, ...current])];
    domainBox.innerHTML = all
      .map((d, i) => `<label class="choice"><input type="checkbox" name="domain" value="${esc(d)}" id="edit-domain-${i}" ${current.has(d) ? "checked" : ""}> ${esc(d)}</label>`)
      .join("");
  };
  renderDomains([]);
  controlPlaneFetch("/api/v1/proposal-domains")
    .then(({ items }) => renderDomains(items))
    .catch(() => {});

  /* ---- Proposal checklist: advice under each field, shown from the start ---- */
  mountFieldAdvice(form, answers, { selfId: task.id, showAll: true });

  function answers() {
    const data = new FormData(form);
    const out = Object.fromEntries(data.entries());
    out.domain = data.getAll("domain");
    const people = contributors.value();
    out.name = people.name;
    out.affiliation = people.affiliation;
    out.github = task.github;
    return out;
  }

  /* ---- Validation display ---- */
  function showErrors(errors) {
    form.querySelectorAll("[data-field]").forEach((wrap) => {
      const key = wrap.dataset.field;
      let msg = wrap.querySelector(":scope > .field-error");
      if (errors[key]) {
        if (!msg) {
          msg = document.createElement("p");
          msg.className = "field-error";
          msg.setAttribute("role", "alert");
          wrap.appendChild(msg);
        }
        msg.textContent = errors[key];
        wrap.classList.add("is-invalid");
      } else {
        msg?.remove();
        wrap.classList.remove("is-invalid");
      }
    });
    const first = Object.keys(errors)[0];
    if (first) form.querySelector(`[data-field="${first}"]`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  /* ---- Character counters -----------------------------------
     An existing proposal can sit a few characters under its cap
     (one is at 11,996 of 12,000), so an author who starts typing
     would otherwise learn about the limit from a 422 after the
     fact. Show the remaining headroom while they write. */
  const NEAR_LIMIT = 0.9;
  const counters = new Map();
  for (const key of Object.keys(LIMITS)) {
    const input = form.elements[key];
    const label = form.querySelector(`[data-field="${key}"] > label`);
    if (!input || !label || input instanceof RadioNodeList) continue;
    const el = document.createElement("span");
    el.className = "counter";
    el.setAttribute("aria-hidden", "true");
    label.appendChild(el);
    counters.set(key, el);
  }
  function updateCounters() {
    for (const [key, el] of counters) {
      const { min, max } = LIMITS[key];
      const len = String(form.elements[key]?.value ?? "").trim().length;
      const over = len > max;
      const near = !over && len >= max * NEAR_LIMIT;
      if (over) el.textContent = `${len} / ${max} max`;
      else if (near) el.textContent = `${max - len} left`;
      else if (min > 0 && len < min) el.textContent = `${len} / ${min} min`;
      else el.textContent = String(len);
      el.classList.toggle("is-over", over);
      el.classList.toggle("is-near", near);
      el.classList.toggle("is-met", !over && !near && min > 0 && len >= min);
    }
  }

  /* ---- Preview ---- */
  let timer = null;
  function renderPreview() {
    const md = buildMarkdown(answers());
    markdown.textContent = md;
    rendered.innerHTML = renderRich(md);
    void mountMath(rendered);
  }
  function schedulePreview() {
    clearTimeout(timer);
    timer = setTimeout(renderPreview, 250);
  }
  form.addEventListener("input", (e) => {
    const key = e.target?.name;
    const wrap = key && form.querySelector(`[data-field="${key}"]`);
    if (wrap?.classList.contains("is-invalid")) {
      const errors = validateAnswers(answers());
      wrap.querySelector(":scope > .field-error")?.remove();
      wrap.classList.toggle("is-invalid", Boolean(errors[key]));
    }
    updateCounters();
    schedulePreview();
  });
  root.querySelectorAll(".preview-toggle__btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      root.querySelectorAll(".preview-toggle__btn").forEach((b) => {
        const active = b === btn;
        b.classList.toggle("is-active", active);
        b.setAttribute("aria-pressed", String(active));
      });
      rendered.hidden = btn.dataset.view !== "rendered";
      markdown.hidden = btn.dataset.view !== "markdown";
    });
  });
  renderPreview();
  updateCounters();

  /* ---- Unsaved-change protection ----------------------------
     A full proposal is a long piece of writing; losing it to a
     stray click or a closed tab is the worst outcome this screen
     has. Leaving the page is caught by the browser's own prompt,
     and Cancel asks for a second, deliberate click in-page rather
     than throwing a modal dialog. */
  let dirty = false;
  let confirmingCancel = false;
  const warnOnUnload = (event) => {
    if (!dirty) return;
    event.preventDefault();
    event.returnValue = "";
  };
  window.addEventListener("beforeunload", warnOnUnload);
  const markDirty = () => {
    dirty = true;
    // Further typing revokes a pending "click Cancel again" confirmation.
    confirmingCancel = false;
  };
  form.addEventListener("input", markDirty);
  form.addEventListener("change", markDirty);

  /** Detach page-level listeners when the editor goes away. */
  function teardown() {
    window.removeEventListener("beforeunload", warnOnUnload);
    clearTimeout(timer);
  }

  /* ---- Cancel ---- */
  root.querySelectorAll("[data-edit-cancel]").forEach((b) =>
    b.addEventListener("click", () => {
      if (dirty && !confirmingCancel) {
        confirmingCancel = true;
        setStatus("You have unsaved changes. Click Cancel again to discard them.", "error");
        return;
      }
      teardown();
      onCancel?.();
    })
  );

  /* ---- Save ---- */
  const setStatus = (message, tone = "") => {
    status.textContent = message;
    status.className = `submit-status${tone ? ` is-${tone}` : ""}`;
  };
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const errors = validateAnswers(answers());
    showErrors(errors);
    if (Object.keys(errors).length) {
      setStatus("Some fields need attention before saving.", "error");
      return;
    }
    saveBtn.disabled = true;
    setStatus("Saving your changes…");
    try {
      // The route is a full replacement, so a proposal that changed while this
      // editor was open (a sync run, or the author in a second tab) would be
      // silently overwritten. Re-read first and stop rather than clobber.
      if (await hasChangedUpstream()) {
        saveBtn.disabled = false;
        setStatus(
          "This proposal changed since you opened the editor — saving now would overwrite that newer version. Reload the page to pick up the latest text, then reapply your edits.",
          "error"
        );
        return;
      }
      const method = (await editMethod()) || "PUT";
      const result = await controlPlaneFetch(`/api/v1/proposals/${encodeURIComponent(task.id)}`, {
        method,
        body: JSON.stringify(buildProposalSubmission(answers())),
      });
      dirty = false;
      teardown();
      setStatus("Saved. The task page and the Discussion now show your changes.", "success");
      onSaved?.(result);
    } catch (error) {
      saveBtn.disabled = false;
      setStatus(saveErrorMessage(error), "error");
      // Only a genuinely absent route justifies sending the author to GitHub.
      if ([405, 501].includes(error?.status)) showFallback();
    }
  });

  /** True when the stored proposal moved on since this editor was opened. */
  async function hasChangedUpstream() {
    try {
      invalidateTasks();
      const fresh = await getTask(task.id);
      if (!fresh?.updated_at || !task.updated_at) return false;
      return fresh.updated_at !== task.updated_at;
    } catch {
      // A failed freshness check must not block a legitimate save.
      return false;
    }
  }

  function showFallback() {
    if (root.querySelector(".proposal-editor__fallback")) return;
    const box = document.createElement("div");
    box.className = "notice proposal-editor__fallback";
    box.innerHTML = `<div>
      <p><strong>Until then, edit the proposal Discussion on GitHub.</strong> Changes made there appear on this page after the next sync. Use "Markdown" above to copy your revised text.</p>
      ${task.discussion_url ? `<p style="margin-top:0.6rem;"><a class="btn btn--secondary" href="${esc(task.discussion_url)}" target="_blank" rel="noopener">Open the Discussion on GitHub</a></p>` : ""}
    </div>`;
    root.querySelector(".proposal-editor__actions").after(box);
  }
}
