/* ============================================================
   AI4S-Benchmark · Front page
   Data-driven readout, roadmap and news, plus the cinematic
   layer: the orbit field on the dark acts, the statement that
   lights up word by word, and the pinned process stage where
   scrolling advances time inside the scene. Everything degrades
   to a static, fully visible page without JS or with reduced
   motion.
   ============================================================ */

import { getSite, getTasks, getReleases, getNews, ROOT } from "../data.js";
import { esc } from "../components.js";

const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const DATE_FMT = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric" });

function formatDeadline(iso) {
  if (!iso) return null;
  const d = new Date(`${iso}T00:00:00`);
  return Number.isNaN(d.getTime()) ? iso : DATE_FMT.format(d);
}

/* ---- Status readout: one glass strip under the headline ---- */
async function renderReadout() {
  const [site, tasks, releases] = await Promise.all([getSite(), getTasks(), getReleases()]);

  const current = releases.find((r) => r.status === "current");
  const preparing = releases.find((r) => r.status === "preparing");
  const release = current
    ? `${current.version} current`
    : preparing
      ? `${preparing.version} preparing`
      : "pre-release";

  const phaseIndex = Math.max(0, site.roadmap.findIndex((p) => p.current));
  const phase = site.roadmap[phaseIndex];
  const released = tasks.filter((t) => t.status === "released").length;
  const underReview = tasks.filter((t) => t.status === "under_review").length;
  const next = (site.submissions ?? [])[0];
  const deadline = formatDeadline(next?.deadline);

  const rows = [
    { label: "Phase", value: phase ? `0${phaseIndex + 1} · ${phase.title}` : "Bench" },
    { label: "Release", value: release, live: true },
    { label: "Tasks", value: released > 0 ? `${tasks.length} public · ${underReview} in review` : `${tasks.length} candidate · ${underReview} in review` },
    deadline ? { label: "Proposals", value: `Open until ${deadline}` } : null,
    next ? { label: "Next stop", value: next.venue } : null,
  ].filter(Boolean);

  document.getElementById("hero-readout").innerHTML = rows
    .map(
      (r) => `<div><dt>${esc(r.label)}</dt><dd>${r.live ? '<span class="hm-dot" aria-hidden="true"></span>' : ""}${esc(r.value)}</dd></div>`
    )
    .join("");
}

/* ---- Affiliation band ----
   The markup holds one group of institution marks. Clone it until the track is
   wide enough that the loop never exposes a gap, then hand the copy count and a
   constant-speed duration to the CSS animation. Without JS the group simply
   sits centred and still. */
async function renderLogoBand() {
  const track = document.getElementById("logo-band-track");
  const group = track?.firstElementChild;
  if (!group) return;

  // Widths depend on the loaded images, so measure only once they have settled
  await Promise.all(
    [...group.querySelectorAll("img")].map((img) =>
      img.complete
        ? Promise.resolve()
        : new Promise((done) => {
            img.addEventListener("load", done, { once: true });
            img.addEventListener("error", done, { once: true });
          })
    )
  );

  const groupWidth = group.getBoundingClientRect().width;
  if (!groupWidth) return;

  const copies = Math.max(2, Math.ceil((window.innerWidth * 2) / groupWidth));
  for (let i = 1; i < copies; i++) {
    const clone = group.cloneNode(true);
    clone.setAttribute("aria-hidden", "true");
    track.appendChild(clone);
  }

  track.style.setProperty("--band-copies", copies);
  track.style.setProperty("--band-duration", `${Math.round(groupWidth / 26)}s`);
  track.classList.add("is-rolling");
}

/* ---- Roadmap ----
   One line of stops: the three phases, with the two publication milestones
   sitting between Bench and Dataset. Milestones are marked differently so they
   read as submissions along the way, not as phases of their own. */
function stop({ kicker, title, text, modifier }, i) {
  return `<li class="hm-stop${modifier} rv" style="--d:${i * 90}ms">
    <span class="hm-label">${esc(kicker)}</span>
    <h3>${esc(title)}</h3>
    <p>${esc(text)}</p>
  </li>`;
}

async function renderRoadmap() {
  const site = await getSite();
  const phases = site.roadmap.map((phase, i) => ({
    kicker: phase.current ? `Phase 0${i + 1} · now` : `Phase 0${i + 1}`,
    title: phase.title,
    text: phase.short ?? phase.summary,
    modifier: phase.current ? " hm-stop--now" : "",
  }));
  const milestones = (site.submissions ?? []).map((s) => ({
    kicker: "Submission",
    title: s.venue,
    text: s.short ?? s.when,
    modifier: " hm-stop--pub",
  }));
  // Bench → submissions → Dataset → Challenge
  const stops = [phases[0], ...milestones, ...phases.slice(1)].filter(Boolean);
  const el = document.getElementById("roadmap");
  el.innerHTML = stops.map(stop).join("");
  observeReveals(el);
}

/* ---- News ---- */
async function renderNews() {
  const news = await getNews();
  const el = document.getElementById("news-list");
  if (news.length === 0) {
    el.closest("section").hidden = true;
    return;
  }
  el.innerHTML = news
    .slice(0, 4)
    .map(
      (n) => `<li>
        <time datetime="${esc(n.date)}">${esc(n.date)}</time>
        <div>
          <h3>${n.link ? `<a href="${ROOT}${esc(n.link)}">${esc(n.title)}</a>` : esc(n.title)}</h3>
          <p>${esc(n.text)}</p>
        </div>
      </li>`
    )
    .join("");
}

/* ============================================================
   Cinematic layer
   ============================================================ */

/* ---- Orbit field: the brand mark at the scale of a sky ---- */
function orbitField(canvas, { density = 1, speed = 1 } = {}) {
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  let w, h, dpr, stars = [];
  const t0 = performance.now();
  const orbits = [
    { rx: 0.42, ry: 0.42, rot: 0, dash: [1, 9], width: 4, alpha: 0.18, sat: [{ a: 0.0, r: 6, c: "#2474FF" }, { a: 3.4, r: 3, c: "#F7F9FC" }], spd: 0.05 },
    { rx: 0.31, ry: 0.15, rot: -0.55, dash: [4, 7], width: 1, alpha: 0.35, sat: [{ a: 1.2, r: 4, c: "#18B7C9" }], spd: -0.08 },
    { rx: 0.2, ry: 0.2, rot: 0, dash: [], width: 1, alpha: 0.22, sat: [{ a: 2.1, r: 2.5, c: "#7E8AA3" }], spd: 0.12 },
    { rx: 0.56, ry: 0.56, rot: 0, dash: [], width: 1, alpha: 0.08, sat: [], spd: 0 },
  ];
  function size() {
    dpr = Math.min(2, window.devicePixelRatio || 1);
    w = canvas.clientWidth; h = canvas.clientHeight;
    canvas.width = w * dpr; canvas.height = h * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    stars = Array.from({ length: Math.round(((w * h) / 9000) * density) }, () => ({
      x: Math.random() * w, y: Math.random() * h, r: Math.random() * 1.1 + 0.3, p: Math.random() * 6.28, s: 0.4 + Math.random() * 0.6,
    }));
  }
  let visible = true;
  function frame(now) {
    const t = (now - t0) / 1000;
    ctx.clearRect(0, 0, w, h);
    const cx = w / 2, cy = h * 0.5, R = Math.min(w, h) * 0.62;
    for (const s of stars) {
      ctx.fillStyle = `rgba(200,214,240,${0.25 + 0.35 * Math.sin(t * s.s + s.p)})`;
      ctx.beginPath(); ctx.arc(s.x, s.y, s.r, 0, 6.28); ctx.fill();
    }
    for (const o of orbits) {
      ctx.save(); ctx.translate(cx, cy); ctx.rotate(o.rot);
      ctx.strokeStyle = `rgba(160,178,214,${o.alpha})`; ctx.lineWidth = o.width; ctx.setLineDash(o.dash);
      ctx.beginPath(); ctx.ellipse(0, 0, R * o.rx, R * o.ry, 0, 0, 6.28); ctx.stroke();
      ctx.setLineDash([]);
      for (const s of o.sat) {
        const a = s.a + t * o.spd * speed;
        const x = Math.cos(a) * R * o.rx, y = Math.sin(a) * R * o.ry;
        const g = ctx.createRadialGradient(x, y, 0, x, y, s.r * 5);
        g.addColorStop(0, s.c); g.addColorStop(1, "rgba(0,0,0,0)");
        ctx.globalAlpha = 0.35; ctx.fillStyle = g; ctx.beginPath(); ctx.arc(x, y, s.r * 5, 0, 6.28); ctx.fill();
        ctx.globalAlpha = 1; ctx.fillStyle = s.c; ctx.beginPath(); ctx.arc(x, y, s.r, 0, 6.28); ctx.fill();
      }
      ctx.restore();
    }
    const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, 60);
    g.addColorStop(0, "rgba(247,249,252,0.35)"); g.addColorStop(1, "rgba(247,249,252,0)");
    ctx.fillStyle = g; ctx.beginPath(); ctx.arc(cx, cy, 60, 0, 6.28); ctx.fill();
    ctx.fillStyle = "#F7F9FC"; ctx.beginPath(); ctx.arc(cx, cy, 3.5, 0, 6.28); ctx.fill();
    if (!reduced && visible) requestAnimationFrame(frame);
  }
  // Only animate while on screen: two canvases must not cost anything off-screen.
  new IntersectionObserver((es) => {
    const wasVisible = visible;
    visible = es.some((e) => e.isIntersecting);
    if (visible && !wasVisible && !reduced) requestAnimationFrame(frame);
  }).observe(canvas);
  size();
  window.addEventListener("resize", () => { size(); if (reduced) frame(performance.now()); }, { passive: true });
  requestAnimationFrame(frame);
}

/* ---- Reveal from rest ---- */
const revealIO = new IntersectionObserver(
  (es) => es.forEach((e) => { if (e.isIntersecting) { e.target.classList.add("in"); revealIO.unobserve(e.target); } }),
  { threshold: 0.15, rootMargin: "0px 0px -6% 0px" }
);
function observeReveals(root = document) {
  root.querySelectorAll(".rv").forEach((el) => revealIO.observe(el));
}

/* ---- Statement: words light up as the paragraph passes through the viewport ---- */
const statementEl = document.getElementById("statement");
let words = [];
if (statementEl) {
  const hi = new Set((statementEl.dataset.hi || "").split(","));
  statementEl.innerHTML = statementEl.textContent
    .trim()
    .split(/\s+/)
    .map((wd) => `<span class="w${hi.has(wd.replace(/[^a-z]/gi, "").toLowerCase()) ? " hi" : ""}">${esc(wd)}</span>`)
    .join(" ");
  words = [...statementEl.querySelectorAll(".w")];
}
function statementTick() {
  if (!words.length) return;
  const r = statementEl.getBoundingClientRect(), vh = window.innerHeight;
  const p = clamp((vh * 0.85 - r.top) / (r.height + vh * 0.45), 0, 1);
  const lit = Math.round(p * words.length);
  words.forEach((el, i) => el.classList.toggle("on", i < lit));
}

/* ---- Process stage: scroll advances time inside a pinned scene ---- */
const stage = document.getElementById("stage");
const steps = [...document.querySelectorAll("#steps .hm-step")];
const bars = [...document.querySelectorAll("#stage-progress i")];
const prog = document.getElementById("prog");
const nodesG = document.getElementById("nodes");
const N = steps.length;
const NODE_LABELS = ["Submit", "Review", "Task PR", "Testing", "Release"];
const nodes = [];
if (nodesG) {
  const NS = "http://www.w3.org/2000/svg";
  for (let i = 0; i < N; i++) {
    // five nodes on the ring, from the top clockwise through three quarters
    const a = -Math.PI / 2 + (i / (N - 1)) * (Math.PI * 1.5);
    const x = 200 + Math.cos(a) * 150, y = 200 + Math.sin(a) * 150;
    const halo = document.createElementNS(NS, "circle");
    halo.setAttribute("class", "halo"); halo.setAttribute("cx", x); halo.setAttribute("cy", y); halo.setAttribute("r", 9);
    halo.style.transformOrigin = `${x}px ${y}px`;
    const c = document.createElementNS(NS, "circle");
    c.setAttribute("class", "node"); c.setAttribute("cx", x); c.setAttribute("cy", y); c.setAttribute("r", 7);
    const tx = document.createElementNS(NS, "text");
    tx.setAttribute("class", "nlabel"); tx.textContent = `0${i + 1} ${NODE_LABELS[i] ?? ""}`;
    tx.setAttribute("x", 200 + Math.cos(a) * 186); tx.setAttribute("y", 200 + Math.sin(a) * 186 + 3.5);
    tx.setAttribute("text-anchor", Math.cos(a) > 0.3 ? "start" : Math.cos(a) < -0.3 ? "end" : "middle");
    nodesG.append(halo, c, tx); nodes.push({ halo, c, tx });
  }
}
let active = -1;
function setActive(i, p) {
  if (prog) prog.style.strokeDashoffset = 1 - p;
  if (i === active) return;
  active = i;
  steps.forEach((s, k) => s.classList.toggle("on", k === i));
  bars.forEach((b, k) => b.classList.toggle("on", k <= i));
  nodes.forEach((n, k) => {
    n.c.classList.toggle("on", k <= i); n.c.classList.toggle("now", k === i);
    n.halo.classList.toggle("now", k === i); n.tx.classList.toggle("on", k <= i);
  });
}
function stageTick() {
  if (!stage) return;
  const r = stage.getBoundingClientRect(), vh = window.innerHeight;
  const p = clamp(-r.top / (r.height - vh), 0, 1);
  setActive(Math.min(N - 1, Math.floor(p * N)), p);
}
if (stage && reduced) {
  stage.classList.add("flat");
  steps.forEach((s) => s.classList.add("on"));
}

let raf = 0;
function onScroll() {
  if (raf) return;
  raf = requestAnimationFrame(() => { raf = 0; statementTick(); if (!reduced) stageTick(); });
}

/* ---- Boot ---- */
orbitField(document.getElementById("orbit-hero"));
orbitField(document.getElementById("orbit-finale"), { density: 0.6, speed: 0.6 });
observeReveals();
window.addEventListener("scroll", onScroll, { passive: true });
window.addEventListener("resize", onScroll, { passive: true });
onScroll();

Promise.all([renderReadout(), renderLogoBand(), renderRoadmap(), renderNews()]).catch((err) =>
  console.error("Homepage render failed:", err)
);
