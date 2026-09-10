/* ============================================================
   AI4S-Benchmark · Releases
   ============================================================ */

import { getReleases, ROOT } from "../data.js";
import { esc, emptyState } from "../components.js";

const STATUS_BADGE = {
  preparing: '<span class="badge badge--preparing">Preparing</span>',
  current: '<span class="badge badge--current">Current</span>',
  archived: '<span class="badge badge--archived">Archived</span>',
};

function stat(label, value, mono = true) {
  return `<div class="stat">
    <span class="stat__value${mono ? "" : ""}">${value != null ? esc(value) : "—"}</span>
    <span class="stat__label">${esc(label)}</span>
  </div>`;
}

getReleases()
  .then((releases) => {
    const el = document.getElementById("release-list");
    if (releases.length === 0) {
      el.innerHTML = emptyState({
        title: "No releases yet",
        text: "The first versioned release is assembled once pilot tasks pass scientific review and agent testing.",
        actionsHTML: `<a class="btn btn--primary" href="${ROOT}tasks/">See candidate tasks</a>`,
      });
      return;
    }

    el.innerHTML = releases
      .map(
        (r) => `<article class="card release-card" style="margin-bottom: var(--space-5);">
        <div>
          <p class="release-card__version">${esc(r.version)}</p>
          ${STATUS_BADGE[r.status] ?? ""}
          ${r.date ? `<p class="mono text-muted" style="margin-top: var(--space-3); font-size: var(--text-xs);">${esc(r.date)}</p>` : `<p class="text-muted" style="margin-top: var(--space-3); font-size: var(--text-xs);">Date to be announced</p>`}
        </div>
        <div>
          <p class="text-secondary" style="margin-bottom:0;">${esc(r.summary ?? "")}</p>
          ${
            r.status === "preparing"
              ? `<p class="text-muted" style="font-size: var(--text-sm); margin-top: var(--space-3);">Task counts, evaluated agents and release notes are published when the release ships — each release freezes exactly what was evaluated, so results stay reproducible.</p>`
              : `<div class="release-card__stats">
                  ${stat("Tasks", r.tasks)}
                  ${stat("Domains", r.domains)}
                  ${stat("Agents evaluated", r.agents_evaluated)}
                  ${stat("Contributors", (r.contributors ?? []).length || null)}
                </div>`
          }
          <div style="display:flex; gap: var(--space-3); flex-wrap:wrap; margin-top: var(--space-4);">
            ${r.release_notes ? `<a class="btn btn--secondary btn--sm" href="${esc(r.release_notes)}" target="_blank" rel="noopener">Release notes <svg class="ext-arrow" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M4.75 11.25 11.25 4.75M5.9 4.75h5.35v5.35"/></svg></a>` : ""}
            ${r.report ? `<a class="btn btn--secondary btn--sm" href="${esc(r.report)}" target="_blank" rel="noopener">Report <svg class="ext-arrow" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M4.75 11.25 11.25 4.75M5.9 4.75h5.35v5.35"/></svg></a>` : ""}
            ${r.github_tag ? `<a class="btn btn--secondary btn--sm mono" href="${esc(r.github_tag)}" target="_blank" rel="noopener">GitHub tag <svg class="ext-arrow" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M4.75 11.25 11.25 4.75M5.9 4.75h5.35v5.35"/></svg></a>` : ""}
          </div>
        </div>
      </article>`
      )
      .join("");
  })
  .catch((err) => console.error("Releases page failed:", err));
