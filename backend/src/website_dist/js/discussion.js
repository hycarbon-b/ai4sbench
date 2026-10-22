/* ============================================================
   AI4S-Benchmark · Reviews from the proposal Discussion

   Every review is a reply on the proposal's GitHub Discussion. The
   control plane keeps only the latest one, so the full list is read
   straight from GitHub's public REST API (no sign-in, CORS-enabled,
   60 requests/hour per visitor IP). Results are cached for the tab
   session; a review published from this site is added to that cache
   immediately, so it shows without waiting for GitHub's 60 s cache.
   ============================================================ */

import { parseReviewComment } from "./reviews.js?v=20261014-release1";

const TTL_MS = 2 * 60 * 1000;
const KEY = (repo, number) => `ai4sbench:discussion:${repo}#${number}`;

/** { repo: "owner/name", number } from a Discussion URL, or null. */
export function discussionRef(url) {
  const m = String(url || "").match(/github\.com\/([^/]+\/[^/]+)\/discussions\/(\d+)/);
  return m ? { repo: m[1], number: Number(m[2]) } : null;
}

function readCache(key) {
  try {
    const hit = JSON.parse(sessionStorage.getItem(key) || "null");
    return hit && Date.now() - hit.at < TTL_MS ? hit : null;
  } catch {
    return null;
  }
}
function writeCache(key, value) {
  try {
    sessionStorage.setItem(key, JSON.stringify({ ...value, at: Date.now() }));
  } catch {
    /* storage unavailable: just refetch next time */
  }
}

function toItem(comment) {
  const review = parseReviewComment(comment.body);
  if (!review) return null;
  return {
    login: comment.user?.login || "",
    avatar: comment.user?.avatar_url || "",
    url: comment.html_url,
    created_at: comment.created_at,
    updated_at: comment.updated_at,
    review,
  };
}

/**
 * All review replies on the Discussion, oldest first, plus the number
 * of other (non-review) replies. Throws when GitHub is unreachable or
 * rate-limited; callers fall back to the control plane's latest review.
 */
export async function loadDiscussionReviews(discussionUrl, { fresh = false } = {}) {
  const ref = discussionRef(discussionUrl);
  if (!ref) return { reviews: [], otherReplies: 0 };
  const key = KEY(ref.repo, ref.number);
  if (!fresh) {
    const hit = readCache(key);
    if (hit) return { reviews: hit.reviews, otherReplies: hit.otherReplies };
  }
  const comments = [];
  for (let page = 1; page <= 5; page++) {
    const res = await fetch(
      `https://api.github.com/repos/${ref.repo}/discussions/${ref.number}/comments?per_page=100&page=${page}`,
      { headers: { Accept: "application/vnd.github+json" } }
    );
    if (!res.ok) {
      const error = new Error(res.status === 403 || res.status === 429 ? "GitHub rate limit reached" : `GitHub returned ${res.status}`);
      error.status = res.status;
      throw error;
    }
    const batch = await res.json();
    comments.push(...batch);
    if (batch.length < 100) break;
  }
  const reviews = comments.map(toItem).filter(Boolean);
  const value = { reviews, otherReplies: comments.length - reviews.length };
  // Keep reviews this tab published that GitHub's cache has not caught up with.
  const pending = readPending(key).filter((p) => !reviews.some((r) => r.url === p.url));
  value.reviews = [...reviews, ...pending];
  writeCache(key, value);
  return value;
}

const PENDING = (key) => `${key}:published`;
function readPending(key) {
  try {
    return JSON.parse(sessionStorage.getItem(PENDING(key)) || "[]");
  } catch {
    return [];
  }
}

/** Record a review this visitor just published so it shows at once. */
export function rememberPublished(discussionUrl, item) {
  const ref = discussionRef(discussionUrl);
  if (!ref) return;
  const key = KEY(ref.repo, ref.number);
  try {
    const pending = readPending(key).filter((p) => p.url !== item.url);
    sessionStorage.setItem(PENDING(key), JSON.stringify([...pending, item]));
    const hit = JSON.parse(sessionStorage.getItem(key) || "null");
    if (hit) {
      hit.reviews = [...hit.reviews.filter((r) => r.url !== item.url), item];
      sessionStorage.setItem(key, JSON.stringify(hit));
    }
  } catch {
    /* ignore */
  }
}
