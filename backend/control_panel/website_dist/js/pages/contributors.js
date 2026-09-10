/* ============================================================
   AI4S-Benchmark · Contributors: credit policy + leaderboard
   ============================================================ */

import { getContributors, getSite, ROOT } from "../data.js";
import { esc, emptyState, chip, ICONS } from "../components.js";

/* ---- Contribution categories ----------------------------------
   The points table in site.json has seven lines; the board folds them into
   four categories so a per-person bar stays readable. Order matters: it is
   the segment order in every bar and the legend, chosen so adjacent colours
   stay distinguishable under colour-vision deficiency. */
const CATEGORIES = [
  { key: "task", label: "Tasks", hint: "Tasks accepted into the benchmark" },
  { key: "review", label: "Reviews", hint: "Formal task reviews" },
  { key: "community", label: "Community", hint: "Organization, referrals and sponsorship" },
  { key: "maintain", label: "Maintenance", hint: "Infrastructure, documentation and website" },
];

const ROLE_CLASS = {
  "Contributor": "role--contributor",
  "Active Contributor": "role--active",
  "Core Contributor": "role--core",
  "Maintainer": "role--maintainer",
};

const SORTS = {
  points: { label: "Points", cmp: (a, b) => b.points - a.points || a.name.localeCompare(b.name) },
  tasks: { label: "Tasks authored", cmp: (a, b) => b.tasks - a.tasks || b.points - a.points },
  reviews: { label: "Reviews", cmp: (a, b) => b.reviews - a.reviews || b.points - a.points },
  name: { label: "Name", cmp: (a, b) => a.name.localeCompare(b.name) },
  newest: { label: "Newest", cmp: (a, b) => (b.since ?? "").localeCompare(a.since ?? "") || b.points - a.points },
};

/* ---- Data shaping ---------------------------------------------- */

function normalize(raw) {
  const breakdown = {};
  for (const c of CATEGORIES) breakdown[c.key] = Number(raw.breakdown?.[c.key] ?? 0) || 0;
  const summed = Object.values(breakdown).reduce((s, v) => s + v, 0);
  const points = Number(raw.points ?? summed) || 0;
  // A record with a total but no breakdown still draws a single-colour bar.
  if (summed === 0 && points > 0) breakdown.task = points;
  return {
    name: String(raw.name ?? "Anonymous"),
    github: raw.github ? String(raw.github).replace(/^@/, "") : null,
    avatar: raw.avatar ?? null,
    institution: raw.institution ?? "",
    role: raw.role ?? "Contributor",
    areas: Array.isArray(raw.areas) ? raw.areas : [],
    points,
    breakdown,
    tasks: raw.tasks_authored?.length ?? 0,
    reviews: raw.tasks_reviewed?.length ?? 0,
    since: raw.since ?? null,
  };
}

/** `?demo=1` swaps in the sample board so the design can be reviewed before
    the first real contributions land. Never used in production data. */
async function loadContributors() {
  const params = new URLSearchParams(window.location.search);
  if (params.get("demo") === "1") {
    const res = await fetch(`${ROOT}data/contributors.sample.json`);
    return { list: (await res.json()).map(normalize), demo: true };
  }
  return { list: (await getContributors()).map(normalize), demo: false };
}

/* ---- Small renderers ------------------------------------------- */

function initials(name) {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0].toUpperCase())
    .join("");
}

function avatar(c) {
  const fallback = `<span class="lb-avatar lb-avatar--initials" aria-hidden="true">${esc(initials(c.name))}</span>`;
  const src = c.avatar ?? (c.github ? `https://github.com/${encodeURIComponent(c.github)}.png?size=96` : null);
  if (!src) return fallback;
  // If the image cannot load (offline, blocked), fall back to the initials mark.
  return `<span class="lb-avatar-host">
    <img class="lb-avatar" src="${esc(src)}" alt="" width="40" height="40" loading="lazy" decoding="async"
      onerror="this.remove()">
    ${fallback}
  </span>`;
}

function roleBadge(role) {
  return `<span class="lb-role ${ROLE_CLASS[role] ?? "role--contributor"}">${esc(role)}</span>`;
}

function rankMark(rank) {
  const cls = rank <= 3 ? ` lb-rank--${rank}` : "";
  return `<span class="lb-rank${cls}">${rank}</span>`;
}

/** Stacked bar. Width is relative to the board leader so rows compare. */
function mixBar(c, max) {
  const total = c.points || 1;
  const scale = max > 0 ? (c.points / max) * 100 : 0;
  const parts = CATEGORIES.filter((cat) => c.breakdown[cat.key] > 0);
  const label = parts.map((cat) => `${cat.label} ${c.breakdown[cat.key]}`).join(", ") || "No points yet";
  return `<span class="lb-mix" role="img" aria-label="${esc(label)}" title="${esc(label)}">
    <span class="lb-mix__track" style="width:${scale.toFixed(1)}%">
      ${parts
        .map(
          (cat) =>
            `<span class="lb-mix__seg lb-mix__seg--${cat.key}" style="flex-grow:${c.breakdown[cat.key] / total}"></span>`
        )
        .join("")}
    </span>
  </span>`;
}

function legend() {
  return `<ul class="lb-legend" aria-label="Contribution categories">
    ${CATEGORIES.map(
      (cat) => `<li><span class="lb-legend__swatch lb-mix__seg--${cat.key}" aria-hidden="true"></span>${esc(cat.label)}<span class="lb-legend__hint"> · ${esc(cat.hint)}</span></li>`
    ).join("")}
  </ul>`;
}

function podium(list, max) {
  const top = list.slice(0, 3);
  if (top.length < 3) return "";
  return `<ol class="lb-podium" aria-label="Top three contributors">
    ${top
      .map(
        (c, i) => `<li class="lb-podium__card lb-podium__card--${i + 1}">
        <div class="lb-podium__head">
          ${rankMark(i + 1)}
          ${avatar(c)}
          <div class="lb-podium__who">
            <span class="lb-name">${nameHTML(c)}</span>
            ${c.institution ? `<span class="lb-affil">${esc(c.institution)}</span>` : ""}
          </div>
        </div>
        <div class="lb-podium__points"><span class="lb-podium__value">${esc(c.points)}</span><span class="lb-podium__unit">points</span></div>
        ${mixBar(c, max)}
        <div class="lb-podium__foot">${roleBadge(c.role)}<span class="lb-podium__counts mono">${esc(c.tasks)} tasks · ${esc(c.reviews)} reviews</span></div>
      </li>`
      )
      .join("")}
  </ol>`;
}

function nameHTML(c) {
  return c.github
    ? `<a href="https://github.com/${esc(c.github)}" target="_blank" rel="noopener">${esc(c.name)}</a>`
    : esc(c.name);
}

function row(c, rank, max) {
  return `<tr>
    <td class="lb-col-rank">${rankMark(rank)}</td>
    <td class="lb-col-who">
      <div class="lb-who">
        ${avatar(c)}
        <div>
          <span class="lb-name">${nameHTML(c)}</span>
          <span class="lb-affil">${esc(c.institution || (c.github ? `@${c.github}` : ""))}</span>
        </div>
      </div>
    </td>
    <td class="lb-col-role">${roleBadge(c.role)}</td>
    <td class="lb-col-areas"><span class="lb-areas">${c.areas.slice(0, 3).map((a) => chip(a)).join("")}${c.areas.length > 3 ? `<span class="chip">+${c.areas.length - 3}</span>` : ""}</span></td>
    <td class="lb-col-mix">${mixBar(c, max)}</td>
    <td class="num">${esc(c.tasks)}</td>
    <td class="num">${esc(c.reviews)}</td>
    <td class="num lb-col-points"><strong>${esc(c.points)}</strong></td>
  </tr>`;
}

/* ---- Leaderboard app ------------------------------------------- */

function renderLeaderboard(all, demo) {
  const host = document.getElementById("leaderboard-app");

  if (all.length === 0) {
    host.innerHTML = emptyState({
      title: "The board opens with the first accepted contributions",
      text: "Contributors are ranked here, with their tasks, reviews, roles and point totals, as soon as the first formal contributions are accepted. Yours could be the first.",
      actionsHTML: `<span class="empty-state__actions">
        <a class="btn btn--primary" href="${ROOT}submit/">Submit a Task</a>
        <a class="btn btn--secondary" href="${ROOT}reviewers/">Become a Reviewer</a>
      </span>`,
    });
    return;
  }

  const state = { query: "", category: "all", sort: "points" };

  host.innerHTML = `
    ${demo ? `<div class="notice lb-demo-note">${ICONS.info}<p><strong>Sample data.</strong> These entries show the design only; the live board is generated from accepted contributions.</p></div>` : ""}
    <div class="lb-controls">
      <div class="lb-rankby" role="group" aria-label="Rank by category">
        <span class="lb-rankby__label">Rank by</span>
        <button type="button" class="filter-toggle" data-category="all" aria-pressed="true">All points</button>
        ${CATEGORIES.map((c) => `<button type="button" class="filter-toggle" data-category="${c.key}" aria-pressed="false"><span class="lb-legend__swatch lb-mix__seg--${c.key}" aria-hidden="true"></span>${esc(c.label)}</button>`).join("")}
      </div>
      <div class="lb-controls__right">
        <label class="search-input">
          <span class="visually-hidden">Search contributors</span>
          ${ICONS.search}
          <input type="search" id="lb-search" placeholder="Search name, institution or field" autocomplete="off">
        </label>
        <div class="select-control">
          <label for="lb-sort">Sort</label>
          <select id="lb-sort">${Object.entries(SORTS).map(([k, s]) => `<option value="${k}">${esc(s.label)}</option>`).join("")}</select>
        </div>
      </div>
    </div>
    <p class="explorer-count" id="lb-count" role="status"></p>
    <div id="lb-podium"></div>
    <div class="table-wrap lb-wrap">
      <table class="data-table lb-table">
        <thead><tr>
          <th scope="col" class="lb-col-rank">#</th>
          <th scope="col">Contributor</th>
          <th scope="col">Role</th>
          <th scope="col">Fields</th>
          <th scope="col" class="lb-col-mix">Contribution mix</th>
          <th scope="col" class="num">Tasks</th>
          <th scope="col" class="num">Reviews</th>
          <th scope="col" class="num">Points</th>
        </tr></thead>
        <tbody id="lb-body"></tbody>
      </table>
    </div>
    ${legend()}
    <p class="lb-foot">Rankings follow the <a href="#credit">credit policy</a>. Points are recorded with each accepted task, review and maintenance contribution, and the board is regenerated from those records.</p>
  `;

  const search = host.querySelector("#lb-search");
  const sortSel = host.querySelector("#lb-sort");
  const body = host.querySelector("#lb-body");
  const count = host.querySelector("#lb-count");
  const podiumHost = host.querySelector("#lb-podium");
  const catButtons = [...host.querySelectorAll("[data-category]")];
  const pointsHeader = host.querySelector("th.num:last-child");

  function visible() {
    const q = state.query.trim().toLowerCase();
    let list = all;
    if (state.category !== "all") {
      // Ranking by one category: score by that category's points alone.
      list = list
        .filter((c) => c.breakdown[state.category] > 0)
        .map((c) => ({ ...c, points: c.breakdown[state.category], breakdown: { [state.category]: c.breakdown[state.category] } }));
    }
    if (q) {
      list = list.filter((c) =>
        [c.name, c.institution, c.github ?? "", c.role, ...c.areas].some((s) => s.toLowerCase().includes(q))
      );
    }
    return [...list].sort(SORTS[state.sort].cmp);
  }

  function update() {
    const list = visible();
    const max = Math.max(0, ...list.map((c) => c.points));
    const activeCat = CATEGORIES.find((c) => c.key === state.category);
    const catLabel = activeCat ? `${activeCat.label.toLowerCase()} only` : "total points";
    pointsHeader.textContent = activeCat ? activeCat.label : "Points";

    count.textContent = list.length === all.length
      ? `${all.length} contributor${all.length === 1 ? "" : "s"} · ranked by ${catLabel}`
      : `${list.length} of ${all.length} contributors · ranked by ${catLabel}`;

    // The podium only makes sense for the natural ranking without a query.
    podiumHost.innerHTML = state.sort === "points" && !state.query.trim() ? podium(list, max) : "";

    body.innerHTML = list.length
      ? list.map((c, i) => row(c, i + 1, max)).join("")
      : `<tr><td colspan="8" class="lb-empty-row">No contributors match. Try another name or field.</td></tr>`;
  }

  search.addEventListener("input", () => { state.query = search.value; update(); });
  sortSel.addEventListener("change", () => { state.sort = sortSel.value; update(); });
  catButtons.forEach((btn) =>
    btn.addEventListener("click", () => {
      state.category = btn.dataset.category;
      catButtons.forEach((b) => b.setAttribute("aria-pressed", String(b === btn)));
      update();
    })
  );
  update();
}

/* ---- Page-head figures ----------------------------------------- */
function renderStats(list) {
  const el = document.getElementById("contrib-stats");
  if (!el) return;
  if (list.length === 0) { el.hidden = true; return; }
  const stats = [
    { value: list.length, label: "Contributors" },
    { value: list.reduce((s, c) => s + c.points, 0), label: "Points awarded" },
    { value: list.reduce((s, c) => s + c.tasks, 0), label: "Tasks authored" },
    { value: list.reduce((s, c) => s + c.reviews, 0), label: "Reviews" },
  ];
  el.innerHTML = stats
    .map((s) => `<div class="stat"><span class="stat__value">${esc(s.value)}</span><span class="stat__label">${esc(s.label)}</span></div>`)
    .join("");
}

/* ---- Points table + roles from site config ---- */
async function renderCredit() {
  const site = await getSite();
  const credit = site.credit;

  document.getElementById("points-table").innerHTML = `<div class="table-wrap"><table class="data-table">
    <thead><tr><th scope="col">Contribution</th><th scope="col">Category</th><th scope="col" class="num">Points</th></tr></thead>
    <tbody>${credit.points
      .map(
        (p) => `<tr><td>${esc(p.contribution)}</td><td>${chip(p.category)}</td><td class="num"><strong>${esc(p.points)}</strong></td></tr>`
      )
      .join("")}</tbody>
  </table></div>`;

  document.getElementById("roles-grid").innerHTML = credit.roles
    .map(
      (r) => `<div class="role-card">
      <h3>${esc(r.name)}</h3>
      <span class="threshold">${esc(r.threshold)}</span>
      <p>${esc(r.description)}</p>
    </div>`
    )
    .join("");

  document.getElementById("committees-note").textContent = credit.committees_note;
  document.getElementById("authorship-note").textContent = credit.authorship_note;
}

async function renderBoard() {
  const { list, demo } = await loadContributors();
  renderStats(list);
  renderLeaderboard(list, demo);
}

Promise.all([renderBoard(), renderCredit()]).catch((err) =>
  console.error("Contributors page failed:", err)
);
