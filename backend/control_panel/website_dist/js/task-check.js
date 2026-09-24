/* ============================================================
   AI4S-Benchmark · Task PR pre-flight check
   The same rules the benchmark repository's PR check enforces
   (scripts/validate_task.py in AI4S-Bench/ai4s-benchmark), plus
   the Terminal-Bench Science conventions that repository follows,
   run before a contributor opens the PR.

   Levels:
     fail — the repository's PR check will reject this
     warn — very likely to come up in review
     tip  — convention worth following
     ok / info — for the report

   Pure functions, no DOM, no network — unit-tested with Node.
   The page (js/pages/preflight.js) gathers files from a local
   folder or from GitHub and hands them here.
   ============================================================ */

/** Exactly the files validate_task.py requires, in its order. */
export const REQUIRED_FILES = ["task.toml", "instruction.md", "environment/Dockerfile", "solution/solve.sh", "tests/test.sh"];

/** [metadata] keys Terminal-Bench Science requires of every task. */
export const RECOMMENDED_METADATA = [
  "author_name",
  "author_email",
  "author_organization",
  "domain",
  "field",
  "subfield",
  "tags",
  "expert_time_estimate_hours",
];

/** Resource limit stated on the proposal form. */
export const LIMITS = { cpus: 16, gpus: 1 };

const SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const TASK_PATH = /^tasks\/([^/]+)\/([^/]+)\/([^/]+)$/;

/* ---- A small TOML reader --------------------------------------
   Enough of TOML for task.toml: tables, dotted table names, bare and
   quoted keys, basic/literal/multi-line strings, numbers, booleans,
   and (multi-line) arrays of those. Anything it cannot read is
   reported, never guessed — Harbor stays the authority on syntax. */
export function parseToml(source) {
  const data = {};
  const errors = [];
  let table = data;
  const text = String(source ?? "").replace(/\r\n?/g, "\n");
  let i = 0;
  let line = 1;

  const peek = () => text[i];
  const skipSpace = () => {
    while (i < text.length && (text[i] === " " || text[i] === "\t")) i++;
  };
  const skipLineRest = () => {
    while (i < text.length && text[i] !== "\n") i++;
  };
  const skipBlank = () => {
    for (;;) {
      skipSpace();
      if (text[i] === "#") skipLineRest();
      if (text[i] === "\n") { i++; line++; continue; }
      break;
    }
  };

  function readString() {
    const q = text[i];
    if (text.startsWith(q.repeat(3), i)) {
      i += 3;
      if (text[i] === "\n") { i++; line++; }
      const end = text.indexOf(q.repeat(3), i);
      if (end < 0) throw new Error("unterminated multi-line string");
      const raw = text.slice(i, end);
      line += (raw.match(/\n/g) ?? []).length;
      i = end + 3;
      return q === '"' ? unescape(raw) : raw;
    }
    i++;
    let out = "";
    while (i < text.length && text[i] !== q) {
      if (text[i] === "\n") throw new Error("newline inside a string");
      if (q === '"' && text[i] === "\\") { out += text.slice(i, i + 2); i += 2; continue; }
      out += text[i++];
    }
    if (text[i] !== q) throw new Error("unterminated string");
    i++;
    return q === '"' ? unescape(out) : out;
  }

  function unescape(s) {
    return s.replace(/\\(u[0-9a-fA-F]{4}|U[0-9a-fA-F]{8}|.)/g, (_, e) => {
      if (e.length > 1) return String.fromCodePoint(parseInt(e.slice(1), 16));
      return { n: "\n", t: "\t", r: "\r", b: "\b", f: "\f", '"': '"', "\\": "\\" }[e] ?? e;
    });
  }

  function readValue() {
    const c = peek();
    if (c === '"' || c === "'") return readString();
    if (c === "[") {
      i++;
      const arr = [];
      for (;;) {
        skipBlank();
        if (peek() === "]") { i++; return arr; }
        arr.push(readValue());
        skipBlank();
        if (peek() === ",") { i++; continue; }
        skipBlank();
        if (peek() === "]") { i++; return arr; }
        throw new Error("expected , or ] in array");
      }
    }
    if (c === "{") {
      i++;
      const obj = {};
      skipSpace();
      if (peek() === "}") { i++; return obj; }
      for (;;) {
        skipSpace();
        const k = readKey();
        skipSpace();
        if (peek() !== "=") throw new Error("expected = in inline table");
        i++;
        skipSpace();
        setPath(obj, k, readValue());
        skipSpace();
        if (peek() === ",") { i++; continue; }
        if (peek() === "}") { i++; return obj; }
        throw new Error("expected , or } in inline table");
      }
    }
    const m = text.slice(i).match(/^[^\s,\]}#]+/);
    if (!m) throw new Error("missing value");
    i += m[0].length;
    const tok = m[0];
    if (tok === "true") return true;
    if (tok === "false") return false;
    const num = Number(tok.replace(/_/g, ""));
    if (!Number.isNaN(num)) return num;
    return tok; // dates and other scalars: kept as text
  }

  function readKey() {
    const parts = [];
    for (;;) {
      skipSpace();
      if (peek() === '"' || peek() === "'") parts.push(readString());
      else {
        const m = text.slice(i).match(/^[A-Za-z0-9_-]+/);
        if (!m) throw new Error("invalid key");
        parts.push(m[0]);
        i += m[0].length;
      }
      // A task.toml may come from anyone's PR: never let a key reach the prototype chain.
      if (["__proto__", "constructor", "prototype"].includes(parts[parts.length - 1])) throw new Error("unsupported key name");
      skipSpace();
      if (peek() === ".") { i++; continue; }
      return parts;
    }
  }

  function setPath(obj, parts, value) {
    let o = obj;
    for (const p of parts.slice(0, -1)) o = o[p] ??= {};
    o[parts[parts.length - 1]] = value;
  }

  while (i < text.length) {
    skipBlank();
    if (i >= text.length) break;
    const start = line;
    try {
      if (peek() === "[") {
        const arrayTable = text[i + 1] === "[";
        i += arrayTable ? 2 : 1;
        const parts = readKey();
        if (text.slice(i, i + (arrayTable ? 2 : 1)) !== (arrayTable ? "]]" : "]")) throw new Error("unclosed table header");
        i += arrayTable ? 2 : 1;
        let o = data;
        for (const p of parts.slice(0, -1)) o = o[p] ??= {};
        const last = parts[parts.length - 1];
        if (arrayTable) {
          (o[last] ??= []).push({});
          table = o[last][o[last].length - 1];
        } else {
          table = o[last] ??= {};
        }
      } else {
        const parts = readKey();
        skipSpace();
        if (peek() !== "=") throw new Error("expected =");
        i++;
        skipSpace();
        setPath(table, parts, readValue());
      }
      skipSpace();
      if (peek() === "#") skipLineRest();
      if (i < text.length && peek() !== "\n") throw new Error("unexpected text after value");
    } catch (error) {
      errors.push({ line: start, message: error.message });
      skipLineRest();
    }
  }
  return { data, errors };
}

/* ---- Locating tasks ------------------------------------------ */

/** Directories (relative paths) that directly contain a task.toml. */
export function findTaskRoots(paths) {
  const roots = new Set();
  for (const p of paths) {
    const parts = p.split("/");
    if (parts[parts.length - 1] === "task.toml") roots.add(parts.slice(0, -1).join("/"));
  }
  return [...roots].sort();
}

/** Task roots touched by a PR's changed files — the same rule as the CI script. */
export function changedTaskRoots(changedPaths) {
  const roots = new Set();
  for (const p of changedPaths) {
    const parts = p.split("/");
    if (parts[0] === "tasks" && parts.length >= 4) roots.add(parts.slice(0, 4).join("/"));
  }
  return [...roots].sort();
}

/** The repository-relative task path hidden in a longer path, if any. */
export function repoTaskPath(path) {
  const parts = String(path ?? "").split("/");
  const at = parts.lastIndexOf("tasks");
  if (at < 0 || parts.length - at < 4) return null;
  return parts.slice(at, at + 4).join("/");
}

/* ---- Proposal link ---------------------------------------------- */

const DISCUSSION_LINK = /https?:\/\/github\.com\/([\w.-]+)\/([\w.-]+)\/discussions\/(\d+)/i;

export function discussionLink(body) {
  const m = String(body ?? "").match(DISCUSSION_LINK);
  return m ? { owner: m[1], repo: m[2], number: Number(m[3]), url: m[0] } : null;
}

/* ---- The check -------------------------------------------------- */

const ok = (message) => ({ level: "ok", message });
const item = (level, message) => ({ level, message });

/**
 * Check one task directory.
 *   files    Map of path-within-the-task → file text, or null when the
 *            file exists but was not read (large or binary).
 *   repoPath the task's path in the repository (tasks/<domain>/<field>/<slug>),
 *            or null when unknown (a folder picked on its own).
 *   pr       { body } when checking a pull request, else null.
 *   proposal { resolve(link) → board item | null } to look up the linked
 *            proposal's status, optional.
 */
export function checkTask({ files, repoPath = null, pr = null, proposal = null, benchRepo = "AI4S-Bench/ai4s-benchmark" }) {
  const has = (p) => files.has(p);
  const read = (p) => (files.has(p) ? files.get(p) : null);
  const groups = [];

  /* 1 · What the repository's PR check enforces */
  const required = REQUIRED_FILES.map((f) => (has(f) ? ok(`<code>${f}</code> is present.`) : item("fail", `<code>${f}</code> is missing.`)));
  const toml = read("task.toml");
  if (has("task.toml")) {
    required.push(
      toml === null
        ? item("info", "<code>task.toml</code> could not be read here, so <code>[metadata]</code> was not checked.")
        : toml.includes("[metadata]")
          ? ok("<code>task.toml</code> has a <code>[metadata]</code> table.")
          : item("fail", "<code>task.toml</code> has no <code>[metadata]</code> table.")
    );
  }
  if (pr) {
    const link = discussionLink(pr.body);
    required.push(
      String(pr.body ?? "").includes("/discussions/")
        ? ok("The PR description links a proposal Discussion.")
        : item("fail", "The PR description does not link the proposal Discussion. Paste its URL under “Task Proposal”.")
    );
    if (link) {
      const inBench = `${link.owner}/${link.repo}`.toLowerCase() === benchRepo.toLowerCase();
      const found = inBench && proposal?.resolve ? proposal.resolve(link) : null;
      if (!inBench) {
        required.push(item("info", `The link points to a Discussion in <code>${link.owner}/${link.repo}</code>, not in <code>${benchRepo}</code>.`));
      } else if (!found) {
        required.push(item("warn", `Discussion #${link.number} is not on the AI4S-Benchmark task board.`));
      } else if (found.approved) {
        required.push(ok(`It links Proposal #${link.number}, which is approved.`));
      } else {
        required.push(
          item("warn", `It links Proposal #${link.number}, whose status is “${found.statusLabel}”. A task PR should follow an approved proposal.`)
        );
      }
    }
  } else {
    required.push(item("info", "When you open the PR, link your approved proposal Discussion in its description; the PR check requires it."));
  }
  groups.push({ title: "Required by the repository's PR check", items: required });

  /* 2 · Location */
  const location = [];
  const m = repoPath ? repoPath.match(TASK_PATH) : null;
  if (repoPath && m) {
    location.push(ok(`Located at <code>${escapeHTML(repoPath)}</code>.`));
  } else if (repoPath) {
    location.push(item("warn", `<code>${escapeHTML(repoPath)}</code> is not of the form <code>tasks/&lt;domain&gt;/&lt;field&gt;/&lt;task-slug&gt;</code>.`));
  } else {
    location.push(item("info", "Place this folder at <code>tasks/&lt;domain&gt;/&lt;field&gt;/&lt;task-slug&gt;/</code> in the benchmark repository."));
  }
  const slug = m ? m[3] : null;
  if (slug) {
    location.push(
      SLUG.test(slug)
        ? ok(`The task slug <code>${escapeHTML(slug)}</code> is lowercase kebab-case.`)
        : item("warn", `The task slug <code>${escapeHTML(slug)}</code> should be lowercase letters, digits and single hyphens.`)
    );
  }
  groups.push({ title: "Location", items: location });

  /* 3 · task.toml */
  const meta = [];
  if (toml !== null && toml !== undefined && has("task.toml")) {
    const { data, errors } = parseToml(toml);
    if (errors.length) {
      meta.push(
        item(
          "warn",
          `This checker could not read ${errors.length === 1 ? "line" : "lines"} ${errors.map((e) => e.line).join(", ")} of <code>task.toml</code> (${escapeHTML(errors[0].message)}). Harbor will report the exact syntax error.`
        )
      );
    }
    const md = typeof data.metadata === "object" && data.metadata ? data.metadata : {};
    const present = RECOMMENDED_METADATA.filter((k) => md[k] !== undefined && md[k] !== "" && !(Array.isArray(md[k]) && !md[k].length));
    const missing = RECOMMENDED_METADATA.filter((k) => !present.includes(k));
    meta.push(
      missing.length
        ? item("tip", `${present.length} of ${RECOMMENDED_METADATA.length} Terminal-Bench Science metadata fields are filled in. Missing: ${missing.map((k) => `<code>${k}</code>`).join(", ")}.`)
        : ok(`All ${RECOMMENDED_METADATA.length} Terminal-Bench Science metadata fields are filled in.`)
    );
    if (m && md.domain && String(md.domain) !== m[1]) {
      meta.push(item("warn", `<code>metadata.domain = "${escapeHTML(md.domain)}"</code> does not match the folder <code>${escapeHTML(m[1])}</code>.`));
    }
    if (m && md.field && String(md.field) !== m[2]) {
      meta.push(item("warn", `<code>metadata.field = "${escapeHTML(md.field)}"</code> does not match the folder <code>${escapeHTML(m[2])}</code>.`));
    }
    const description = data.task?.description;
    meta.push(
      description ? ok("<code>[task]</code> has a description.") : item("tip", "Add a one-line <code>description</code> under <code>[task]</code>.")
    );
    const env = data.environment ?? {};
    if (typeof env.cpus === "number" && env.cpus > LIMITS.cpus) {
      meta.push(item("warn", `<code>environment.cpus = ${env.cpus}</code> exceeds the current limit of ${LIMITS.cpus} CPU cores per task.`));
    }
    if (typeof env.gpus === "number" && env.gpus > LIMITS.gpus) {
      meta.push(item("warn", `<code>environment.gpus = ${env.gpus}</code> exceeds the current limit of ${LIMITS.gpus} GPU per task.`));
    }
    const hasResources = ["cpus", "memory_mb", "gpus"].some((k) => env[k] !== undefined);
    meta.push(
      hasResources
        ? ok("Resources are declared under <code>[environment]</code>.")
        : item("tip", "Declare <code>cpus</code>, <code>memory_mb</code> and <code>gpus</code> under <code>[environment]</code> so the run matrix can schedule the task.")
    );
    const timeouts = [data.agent?.timeout_sec, data.verifier?.timeout_sec];
    meta.push(
      timeouts.every((t) => typeof t === "number")
        ? ok("Agent and verifier timeouts are set.")
        : item("tip", "Set <code>[agent] timeout_sec</code> and <code>[verifier] timeout_sec</code>.")
    );
  } else if (has("task.toml")) {
    meta.push(item("info", "<code>task.toml</code> was not read, so its contents were not checked."));
  } else {
    meta.push(item("info", "No <code>task.toml</code> to inspect."));
  }
  groups.push({ title: "task.toml", items: meta });

  /* 4 · Files */
  const content = [];
  const instruction = read("instruction.md");
  if (instruction !== null) {
    const words = instruction.replace(/^#.*$/gm, "").trim().split(/\s+/).filter(Boolean).length;
    content.push(
      words >= 20
        ? ok(`<code>instruction.md</code> has ${words} words of instructions.`)
        : item("warn", "<code>instruction.md</code> is nearly empty. It is the only thing the agent reads, so state the task, inputs and expected outputs.")
    );
  }
  const docker = read("environment/Dockerfile");
  if (docker !== null) {
    content.push(
      /^\s*FROM\s+\S+/im.test(docker)
        ? ok("<code>environment/Dockerfile</code> starts from a base image.")
        : item("warn", "<code>environment/Dockerfile</code> has no <code>FROM</code> line.")
    );
  }
  for (const script of ["solution/solve.sh", "tests/test.sh"]) {
    const body = read(script);
    if (body === null) continue;
    if (!body.trim()) {
      content.push(item("warn", `<code>${script}</code> is empty.`));
      continue;
    }
    content.push(
      body.startsWith("#!")
        ? ok(`<code>${script}</code> starts with a shebang.`)
        : item("warn", `<code>${script}</code> should start with a shebang such as <code>#!/usr/bin/env bash</code>.`)
    );
    if (/\r\n/.test(body)) {
      content.push(
        item("warn", `<code>${script}</code> has Windows (CRLF) line endings, which break shell scripts inside the Linux container. Convert it to LF.`)
      );
    }
  }
  if (!content.length) content.push(item("info", "No file contents were available to inspect."));
  groups.push({ title: "Files", items: content });

  const levels = groups.flatMap((g) => g.items.map((x) => x.level));
  const verdict = levels.includes("fail") ? "fail" : levels.includes("warn") ? "warn" : "pass";
  return { verdict, groups };
}

function escapeHTML(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}
