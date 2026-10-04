/* ============================================================
   AI4S-Benchmark · Contributors
   The control plane stores one `name` and one `affiliation`
   string per proposal. Several people can share a task, so the
   form joins them with "; " and every reader splits them back.
   Older proposals were typed free-form ("A & B", "A and B"), so
   the splitter also recognises those separators.
   DOM-free; unit-tested with Node.
   ============================================================ */

export const CONTRIBUTOR_SEPARATOR = "; ";

/** Marks an empty affiliation slot inside a joined list. */
const NONE = "—";

const NAME_SEPARATORS = /\s*(?:;|&|、|\+|\/|\band\b|\s+with\s+)\s*|\s*,\s*/;

/** Split a free-form list of people into trimmed names. */
export function parseNames(text) {
  return String(text ?? "")
    .split(NAME_SEPARATORS)
    .map((n) => n.trim())
    .filter(Boolean);
}

/** Affiliations often contain commas, "and" and "&", so only ";" and "、" separate them. */
function parseAffiliations(text) {
  return String(text ?? "")
    .split(/\s*[;、]\s*/)
    .map((a) => a.trim());
}

/**
 * Pair names with affiliations. Rules, in order:
 * one affiliation per name → pair by index; a single affiliation → shared
 * by everyone; anything else → shown on the first person only.
 */
export function splitContributors(name, affiliation) {
  const names = parseNames(name);
  const affiliations = parseAffiliations(affiliation).map((a) => (a === NONE || /^none( provided)?$/i.test(a) ? "" : a));
  const hasAff = affiliations.some(Boolean);
  return names.map((n, i) => {
    let aff = "";
    if (hasAff) {
      if (affiliations.length === names.length) aff = affiliations[i];
      else if (affiliations.length === 1 || names.length === 1) aff = i === 0 || affiliations.length === 1 ? affiliations.join(CONTRIBUTOR_SEPARATOR) : "";
      else aff = i === 0 ? affiliations.filter(Boolean).join(CONTRIBUTOR_SEPARATOR) : "";
    }
    return { name: n, affiliation: aff };
  });
}

/**
 * Join contributor rows into the two strings the control plane accepts.
 * Identical affiliations collapse to one; otherwise slots stay aligned.
 */
export function joinContributors(rows) {
  const people = (rows ?? [])
    .map((r) => ({ name: String(r?.name ?? "").trim(), affiliation: String(r?.affiliation ?? "").trim() }))
    .filter((r) => r.name);
  const name = people.map((p) => p.name).join(CONTRIBUTOR_SEPARATOR);
  const distinct = [...new Set(people.map((p) => p.affiliation).filter(Boolean))];
  let affiliation = "";
  if (distinct.length === 1 && people.every((p) => !p.affiliation || p.affiliation === distinct[0])) {
    affiliation = distinct[0];
  } else if (distinct.length > 1) {
    affiliation = people.map((p) => p.affiliation || NONE).join(CONTRIBUTOR_SEPARATOR);
  }
  return { name, affiliation };
}
