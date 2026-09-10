/* ============================================================
   AI4S-Benchmark · Motion system
   Designed, not defaulted: scroll reveals on one easing curve,
   staggered children, count-up stats, header elevation.
   Progressive enhancement — with JS disabled or reduced motion,
   content renders instantly. Dynamic (data-driven) content is
   caught via a MutationObserver; each container animates once.
   ============================================================ */

const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/* ---- Header elevation on scroll ---- */
const header = document.querySelector(".site-header");
if (header) {
  const onScroll = () => header.classList.toggle("is-scrolled", window.scrollY > 8);
  onScroll();
  window.addEventListener("scroll", onScroll, { passive: true });
}

if (!reduced && "IntersectionObserver" in window) {
  document.documentElement.classList.add("js-motion");

  /* Containers whose direct children reveal in sequence */
  const STAGGER = [
    ".measure-grid", ".lifecycle", ".plan",
    ".role-grid", ".contributor-grid", ".news-list",
    ".page-head__stats", ".task-list",
    ".hero-panel__stats",
  ].join(",");

  /* Elements that reveal individually */
  const SINGLE = [
    ".section-head", ".hero-panel", ".measure-note", ".community",
    ".notice", ".prose > *",
    ".empty-state", ".table-wrap", ".verify-panel", ".release-card",
    ".task-layout__main > section",
    ".task-layout__aside > *",
  ].join(",");

  const seen = new WeakSet();

  const io = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) {
          entry.target.classList.add("is-revealed");
          io.unobserve(entry.target);
        }
      }
    },
    { rootMargin: "0px 0px -8% 0px", threshold: 0.08 }
  );

  function observeEl(el, delay = 0) {
    if (seen.has(el)) return;
    seen.add(el);
    // Skip content already in the upper viewport — no "pop" on load.
    const rect = el.getBoundingClientRect();
    if (rect.top < window.innerHeight * 0.55 && rect.bottom > 0) return;
    if (delay) el.style.setProperty("--reveal-delay", `${Math.min(delay, 420)}ms`);
    el.setAttribute("data-reveal", "");
    io.observe(el);
  }

  function apply(root) {
    if (root.matches?.(SINGLE)) observeEl(root);
    root.querySelectorAll?.(SINGLE).forEach((el) => observeEl(el));

    const staggerContainers = [
      ...(root.matches?.(STAGGER) ? [root] : []),
      ...(root.querySelectorAll ? root.querySelectorAll(STAGGER) : []),
    ];
    for (const parent of staggerContainers) {
      if (parent.dataset.motionDone) continue;
      if (parent.children.length === 0) continue; // wait for data render
      parent.dataset.motionDone = "1";
      [...parent.children].forEach((child, i) => observeEl(child, i * 70));
    }
  }

  apply(document.body);

  /* Data-driven content lands after fetch — animate it exactly once. */
  const mo = new MutationObserver((muts) => {
    for (const m of muts) {
      const host = m.target;
      if (host.nodeType === 1) apply(host);
      for (const node of m.addedNodes) {
        if (node.nodeType === 1) apply(node);
      }
    }
  });
  mo.observe(document.body, { childList: true, subtree: true });

  /* ---- Count-up stats ---- */
  const counted = new WeakSet();
  const countIO = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        const el = entry.target;
        countIO.unobserve(el);
        if (counted.has(el)) continue;
        counted.add(el);
        const finalText = el.textContent.trim();
        if (!/^\d{1,4}$/.test(finalText)) continue;
        const target = Number(finalText);
        if (target === 0) continue;
        const start = performance.now();
        const dur = 900;
        // Failsafe: throttled/occluded tabs may starve rAF — always settle.
        const failsafe = setTimeout(() => { el.textContent = finalText; }, dur + 500);
        const tick = (now) => {
          const t = Math.min(1, (now - start) / dur);
          const eased = 1 - Math.pow(1 - t, 4);
          el.textContent = String(Math.round(target * eased));
          if (t < 1) requestAnimationFrame(tick);
          else clearTimeout(failsafe);
        };
        el.textContent = "0";
        requestAnimationFrame(tick);
      }
    },
    { threshold: 0.4 }
  );
  const observeStats = (root) =>
    root.querySelectorAll?.(".stat__value").forEach((el) => countIO.observe(el));
  observeStats(document.body);
  new MutationObserver((muts) => {
    for (const m of muts) {
      for (const node of m.addedNodes) {
        if (node.nodeType === 1) observeStats(node);
      }
    }
  }).observe(document.body, { childList: true, subtree: true });
}
