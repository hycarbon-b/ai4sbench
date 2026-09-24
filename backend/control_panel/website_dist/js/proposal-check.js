/* ============================================================
   AI4S-Benchmark · Proposal checklist
   Advisory checks that go beyond "is the field long enough"
   (that is validateAnswers() in proposal.js): does the proposal
   give reviewers what they need, and will its math render?

   Nothing here blocks a submission. Each finding is either
     warn — likely to cost a review round if left as is
     tip  — would make the proposal stronger
   and says what to change, in words written for the author.

   Pure functions, no DOM — unit-tested with Node.
   ============================================================ */

export const FIELD_NAMES = {
  title: "Task title",
  field_name: "Specific field",
  problem: "Problem specification",
  solvability: "Solvability",
  references: "References & resources",
  software: "Software and tools",
  dataset: "Dataset & artifacts",
  compute: "Computation resources",
  workflow: "Expected workflow & outputs",
  evaluation: "Evaluation method",
  leakage: "Cheating & leakage risk",
};

const TEXT_FIELDS = ["problem", "solvability", "references", "software", "dataset", "compute", "workflow", "evaluation", "leakage"];

const text = (v) => String(v ?? "").trim();

/* ---- Individual checks -------------------------------------- */

const SOURCE = /https?:\/\/|\bdoi\s*:|\b10\.\d{4,9}\/\S+|\barxiv\s*:?\s*\d{4}\.\d{4,5}|\barxiv\.org|\bisbn\b|\bpmid\b|\bpmc\d+/i;

// A named measure: what is compared or scored.
const MEASURE =
  /\b(error|accuracy|mae|rmse|mse|r\^?2|r²|f1|precision|recall|auc|roc|tolerance|threshold|score|pass(es|ed)?|fail(s|ed)?|metric|correlation|match(es|ed)?|agreement|agree|deviation|rank(ing)?|reward|compar(e|ed|ison)|reference|ground[- ]truth|consisten(t|cy)|residual|converge(nce|s)?|runtime|wall[- ]?time|cost|speed ?up|memory|valid(ate|ity|ation)|verif(y|ier|ication)|correct(ness)?|rubric)\b/i;
// A success criterion: a number, a comparison or an explicit pass rule.
const CRITERION = /\d|%|[<>≤≥]|\\(le|ge|leq|geq|lt|gt)\b|\b(within|below|above|at least|at most|less than|more than|exact(ly)?|must|pass(es)? (if|when)|binary|all tests)\b/i;

// Journal-style citations ("Phys. Rev. Lett. 119, 015701 (2017)", "Smith et al. 2020")
// are checkable even without a link.
const CITATION_YEAR = /\b(19|20)\d{2}\b/;
const CITATION_FORM = /\bet al\b|\b[A-Z][a-z]{0,6}\.\s*[A-Z][a-z]{0,8}\.|\b(nature|science|phys|chem|journal|proc|rev|lett|annu|nucleic|bioinformatics|cell)\b/i;

const EXISTENCE =
  /\b(known|published|reference|literature|analytic(al)?|closed[- ]form|exact|oracle|baseline|ground[- ]truth|benchmark|proven|proof|theorem|verified|validated|reproduc|numerical(ly)?|experiment(al|s)?|data(set)?|simulation|converge|measured|derived|computed|calculat)/i;


// TeX command names that almost never appear bare in prose when followed by
// `{`, `_` or `^` — "frac{1}{2}" or "sigma_x" means the backslash was lost.
const STRIPPED_TEX =
  /(?<![\\\w])(frac|dfrac|tfrac|sqrt|mathrm|mathbf|mathcal|mathbb|operatorname|text|left|right|langle|rangle|sum|prod|int|oint|partial|nabla|alpha|beta|gamma|delta|epsilon|varepsilon|theta|lambda|mu|nu|sigma|omega|psi|phi|varphi|chi|rho|tau|eta|zeta|xi|hbar|cdot|times|infty|hat|bar|tilde|vec|dot)(?=[{_^])/g;

function mathIssues(field, value) {
  const found = [];
  const v = String(value ?? "");
  if (!v) return found;

  const opensParen = (v.match(/\\\(/g) ?? []).length;
  const closesParen = (v.match(/\\\)/g) ?? []).length;
  const opensBracket = (v.match(/\\\[/g) ?? []).length;
  const closesBracket = (v.match(/\\\]/g) ?? []).length;
  if (opensParen !== closesParen || opensBracket !== closesBracket) {
    found.push({
      id: `math-delimiters-${field}`,
      level: "warn",
      field,
      message: `The math delimiters do not pair up (${opensParen + opensBracket} opening \\( or \\[ vs ${closesParen + closesBracket} closing \\) or \\]). A formula will show as raw text until they match.`,
    });
  }

  // Unpaired $: drop $$…$$ blocks and escaped \$ first, then count.
  const singles = v.replace(/\$\$[\s\S]*?\$\$/g, "").replace(/\\\$/g, "").match(/\$/g) ?? [];
  if (singles.length % 2 === 1) {
    found.push({
      id: `math-dollar-${field}`,
      level: "warn",
      field,
      message: "There is an unmatched $. If it starts a formula, close it with a second $, otherwise the rest of the paragraph may render as math.",
    });
  }

  const stripped = [...new Set([...v.matchAll(STRIPPED_TEX)].map((m) => m[1]))];
  if (stripped.length) {
    const sample = stripped.slice(0, 3).map((c) => `“${c}”`).join(", ");
    // Mixed with real TeX commands, a bare "frac{" is a lost backslash and
    // will not render. In an all-plain-text formula it is only a suggestion.
    const hasTeX = /\\[a-zA-Z]+/.test(v);
    found.push({
      id: `math-backslash-${field}`,
      level: hasTeX ? "warn" : "tip",
      field,
      message: hasTeX
        ? `${sample} looks like LaTeX that lost its backslash. Write it as \\${stripped[0]}… inside $…$ so it renders as a formula.`
        : `${sample} reads like a formula written as plain text. Writing it as LaTeX inside $…$ (for example $\\${stripped[0]}…$) renders it as math on the task page.`,
    });
  }
  return found;
}

/* ---- Duplicates ------------------------------------------- */

const STOP = new Set(
  "the a an of for and or in on to with from by at as is are be via into using under over its their this that task tasks benchmark agent agents based".split(" ")
);

export function titleTokens(title) {
  return new Set(
    String(title ?? "")
      .toLowerCase()
      .normalize("NFKD")
      .replace(/[^\p{L}\p{N}]+/gu, " ")
      .split(" ")
      .filter((w) => w.length > 2 && !STOP.has(w))
  );
}

/** Jaccard similarity of two titles' significant words (0–1). */
export function titleSimilarity(a, b) {
  const A = titleTokens(a);
  const B = titleTokens(b);
  if (!A.size || !B.size) return 0;
  let shared = 0;
  for (const w of A) if (B.has(w)) shared++;
  return shared / (A.size + B.size - shared);
}

export const DUPLICATE_THRESHOLD = 0.6;

/** Existing proposals whose titles are near-identical to `title`. */
export function similarProposals(title, existing = [], selfId = null) {
  return existing
    .filter((t) => t && t.id !== selfId)
    .map((t) => ({ task: t, score: titleSimilarity(title, t.title) }))
    .filter((m) => m.score >= DUPLICATE_THRESHOLD)
    .sort((a, b) => b.score - a.score);
}

/* ---- Compute budget ------------------------------------------ */

function computeIssues(value) {
  const v = text(value);
  if (!v) return [];
  const found = [];
  if (!/\d/.test(v)) {
    found.push({
      id: "compute-estimate",
      level: "tip",
      field: "compute",
      message: "Give an order-of-magnitude estimate: wall time, CPU cores, GPU and memory.",
    });
  }
  const gpus = [...v.matchAll(/(\d+)\s*[x×]?\s*(?:nvidia\s+)?(?:gpus?|a100s?|h100s?|v100s?|l40s?|rtx\s*\d+)(?!\s*[·\-*/]?\s*h(?:ours?|rs?)?\b)/gi)].map((m) => Number(m[1]));
  const cores = [...v.matchAll(/(\d+)\s*(?:cpu\s*)?(?:cores?|vcpus?|cpus?)\b/gi)].map((m) => Number(m[1]));
  if (gpus.some((n) => n > 1) || cores.some((n) => n > 16)) {
    found.push({
      id: "compute-limit",
      level: "warn",
      field: "compute",
      message: "The current limit is one GPU and 16 CPU cores per task. Explain how the task fits that budget, or how a smaller version keeps the science.",
    });
  }
  return found;
}

/* ---- The checklist ------------------------------------------ */

/**
 * Run every advisory check on a set of proposal answers (the same shape
 * the submission form and the editor produce). `existing` is the public
 * board, used to spot near-duplicate titles; `selfId` excludes the
 * proposal being edited from that comparison.
 */
export function checkProposal(answers = {}, { existing = [], selfId = null } = {}) {
  const a = Object.fromEntries(Object.keys(FIELD_NAMES).map((k) => [k, text(answers[k])]));
  const findings = [];

  if (a.title) {
    for (const { task } of similarProposals(a.title, existing, selfId).slice(0, 2)) {
      findings.push({
        id: `duplicate-${task.id}`,
        level: "tip",
        field: "title",
        message: `A proposal with a very similar title already exists: ${task.discussion_number ? `#${task.discussion_number} ` : ""}“${task.title}”${task.github ? ` by @${task.github}` : ""}. If you are revising it, edit that proposal from its task page instead of submitting a new one.`,
        taskId: task.id,
      });
    }
  }

  if (a.field_name.length > 80) {
    findings.push({
      id: "field-name-long",
      level: "tip",
      field: "field_name",
      message: "Keep the specific field to a few words (for example “Quantum many-body theory”). It is shown as a tag and used as a filter on the task board.",
    });
  }

  if (a.problem && a.problem.length < 400) {
    findings.push({
      id: "problem-short",
      level: "tip",
      field: "problem",
      message: "Reviewers usually need the background, the precise task the agent must do, and why it matters — typically a few paragraphs.",
    });
  }

  if (a.solvability && !EXISTENCE.test(a.solvability)) {
    findings.push({
      id: "solvability-evidence",
      level: "tip",
      field: "solvability",
      message: "Say how we know a correct answer exists and can be checked: a published result, an analytic solution, a reference implementation or experimental data.",
    });
  }

  if (a.references && !SOURCE.test(a.references)) {
    const cited = CITATION_YEAR.test(a.references) && CITATION_FORM.test(a.references);
    findings.push(
      cited
        ? {
            id: "references-links",
            level: "tip",
            field: "references",
            message: "Your citations are clear. Adding DOIs or links lets reviewers open each source in one click.",
          }
        : {
            id: "references-source",
            level: "tip",
            field: "references",
            message: "If there is a paper, dataset or code this task builds on, citing it (authors and year, a DOI, an arXiv ID or a link) helps reviewers check the science.",
          }
    );
  }

  findings.push(...computeIssues(a.compute));

  if (!a.workflow) {
    findings.push({
      id: "workflow-empty",
      level: "tip",
      field: "workflow",
      message: "Optional — but a sentence on what the agent starts with and what it must hand back helps reviewers picture the task.",
    });
  }

  if (a.evaluation && !MEASURE.test(a.evaluation) && !CRITERION.test(a.evaluation)) {
    findings.push({
      id: "evaluation-measure",
      level: "warn",
      field: "evaluation",
      message: "Name the metric and what counts as success, for example “relative error below 1e-3 against the reference” or “exact match of the reported value”.",
    });
  } else if (a.evaluation && !CRITERION.test(a.evaluation)) {
    findings.push({
      id: "evaluation-threshold",
      level: "tip",
      field: "evaluation",
      message: "You name what is measured — also say what value counts as a pass (a tolerance, a threshold or an exact-match rule), so the verifier can be written from it.",
    });
  }

  if (a.leakage && a.leakage.length < 60) {
    findings.push({
      id: "leakage-detail",
      level: "tip",
      field: "leakage",
      message: "Explain why an agent cannot simply look the answer up, and what stays hidden from it during the run.",
    });
  }

  for (const field of TEXT_FIELDS) findings.push(...mathIssues(field, answers[field]));

  const order = Object.keys(FIELD_NAMES);
  const rank = { warn: 0, tip: 1 };
  return findings.sort((x, y) => rank[x.level] - rank[y.level] || order.indexOf(x.field) - order.indexOf(y.field));
}

/** Short summary line: "2 to fix · 3 suggestions" or "No issues found". */
export function summarize(findings) {
  const warn = findings.filter((f) => f.level === "warn").length;
  const tip = findings.filter((f) => f.level === "tip").length;
  if (!warn && !tip) return "No issues found";
  return [warn ? `${warn} to fix` : "", tip ? `${tip} ${tip === 1 ? "suggestion" : "suggestions"}` : ""].filter(Boolean).join(" · ");
}
