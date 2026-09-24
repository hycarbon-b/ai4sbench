/* ============================================================
   AI4S-Benchmark · Contributor rows (shared form widget)
   Used by the proposal form and the on-site proposal editor.
   Rows hold a name and an optional affiliation; the first row
   is the submitter. Serialisation lives in people.js.
   ============================================================ */

import { splitContributors, joinContributors } from "./people.js?v=20260921-3";

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function rowHTML({ name = "", affiliation = "" }, index) {
  const first = index === 0;
  return `<div class="contrib-row" data-contrib-row>
    <div class="contrib-row__fields">
      <div class="form-field">
        <label for="contrib-name-${index}">${first ? "Name" : `Contributor ${index + 1}`}</label>
        <input type="text" id="contrib-name-${index}" name="contributor_name" maxlength="120" value="${esc(name)}" autocomplete="${first ? "name" : "off"}" placeholder="${first ? "Your name" : "Full name"}">
      </div>
      <div class="form-field">
        <label for="contrib-aff-${index}">Institution / affiliation <span class="optional">(optional)</span></label>
        <input type="text" id="contrib-aff-${index}" name="contributor_affiliation" maxlength="200" value="${esc(affiliation)}" autocomplete="${first ? "organization" : "off"}" placeholder="University, institute or lab">
      </div>
    </div>
    ${first ? "" : `<button type="button" class="contrib-row__remove" data-contrib-remove aria-label="Remove contributor ${index + 1}">Remove</button>`}
  </div>`;
}

/**
 * Mount the widget into `root`. Returns an API:
 *   read()  → [{name, affiliation}]  (rows in order, blanks kept)
 *   value() → {name, affiliation}    (joined for the control plane)
 *   set(rows | {name, affiliation})
 */
export function mountContributorRows(root, initial = [{ name: "", affiliation: "" }], { onChange } = {}) {
  let rows = normalise(initial);

  function normalise(value) {
    if (Array.isArray(value)) return value.length ? value.map((r) => ({ name: r?.name ?? "", affiliation: r?.affiliation ?? "" })) : [{ name: "", affiliation: "" }];
    if (value && typeof value === "object") return splitContributors(value.name, value.affiliation).length ? splitContributors(value.name, value.affiliation) : [{ name: "", affiliation: "" }];
    return [{ name: "", affiliation: "" }];
  }

  function render() {
    root.innerHTML = `
      <div class="contrib-rows">${rows.map(rowHTML).join("")}</div>
      <button type="button" class="btn btn--ghost contrib-add" data-contrib-add>+ Add a co-contributor</button>
      <p class="hint">Everyone listed is credited on the task page and in the Discussion. The GitHub account you sign in with stays the contact for reviewers.</p>`;
    root.querySelector("[data-contrib-add]").addEventListener("click", () => {
      rows = read();
      rows.push({ name: "", affiliation: "" });
      render();
      root.querySelectorAll('input[name="contributor_name"]')[rows.length - 1]?.focus();
      onChange?.();
    });
    root.querySelectorAll("[data-contrib-remove]").forEach((btn, i) => {
      btn.addEventListener("click", () => {
        rows = read();
        rows.splice(i + 1, 1);
        render();
        onChange?.();
      });
    });
  }

  function read() {
    const names = [...root.querySelectorAll('input[name="contributor_name"]')].map((i) => i.value);
    const affs = [...root.querySelectorAll('input[name="contributor_affiliation"]')].map((i) => i.value);
    return names.map((name, i) => ({ name, affiliation: affs[i] ?? "" }));
  }

  render();
  return {
    read,
    value: () => joinContributors(read()),
    set(value) {
      rows = normalise(value);
      render();
    },
    focusFirst: () => root.querySelector('input[name="contributor_name"]')?.focus(),
  };
}
