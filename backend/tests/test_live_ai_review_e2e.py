"""Opt-in live test of the deployed proposal AI review pipeline.

Run from the repository root with:

    $env:AI_REVIEW_LIVE = "1"
    python backend/tests/test_live_ai_review_e2e.py

The test uses GITHUB_TOKEN or GH_TOKEN, then falls back to ``gh auth token``.
It creates a public Task Proposals Discussion, waits for the deployed backend's
AI review comment, and checks the linked Actions run. No token is written to disk.
To resume an interrupted run without creating another Discussion, set
AI_REVIEW_E2E_DISCUSSION_NUMBER to the number printed after creation.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import unittest
import urllib.error
import urllib.request
from datetime import UTC, datetime

REPOSITORY = "AI4S-Bench/ai4s-benchmark"
BACKEND_URL = "https://dashboard.ai4sbench.org"
MARKER = "<!-- ai4sbench-proposal-ai-review:v1 -->"
DECISIONS = {"Strong Reject", "Reject", "Uncertain", "Accept", "Strong Accept"}
WORKFLOW = "proposal-review.yml"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


def request_json(url: str, *, token: str | None = None, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "ai4sbench-live-e2e"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise AssertionError(f"{url.split('?')[0]} returned HTTP {error.code}") from None


def github_token() -> str:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        return token
    result = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=False)
    if result.returncode or not result.stdout.strip():
        raise AssertionError("Set GITHUB_TOKEN or GH_TOKEN, or authenticate with gh auth login")
    return result.stdout.strip()


def graphql(token: str, query: str, variables: dict | None = None) -> dict:
    response = request_json(
        "https://api.github.com/graphql",
        token=token,
        payload={"query": query, "variables": variables or {}},
    )
    if response.get("errors"):
        messages = "; ".join(str(item.get("message", "GraphQL error")) for item in response["errors"])
        raise AssertionError(messages)
    return response["data"]


def proposal_body(login: str) -> str:
    return f"""## Scientific Domain

Applied Mathematics > Inverse Problems

## Scientific Problem
Infer a one-dimensional spatially varying diffusion coefficient from sparse, noisy temperature observations.
The benchmark supplies the forcing, boundary conditions, observation times, and a deterministic forward
solver. The agent must recover the coefficient field on a held-out instance without access to the reference
coefficient.

## Solvability
The supplied examples include a constant-coefficient sanity case and two smooth training fields. A regularized
least-squares or Bayesian inversion can recover the held-out field with the provided solver and observations.
The hidden case uses a fixed random seed and a noise level disclosed in the task specification.

## References & Resources
The mathematical model is the one-dimensional heat equation with Dirichlet boundary conditions. Reference:
A. Tarantola, Inverse Problem Theory and Methods for Model Parameter Estimation, SIAM, 2005. The task package
would include a short derivation, generated observations, and a documented finite-difference solver; no
external data download is needed.

## Environment

### Software and tools
Python 3.12, NumPy, SciPy, and pytest in a pinned container image.

### Dataset & artifacts
Small CSV files contain observation coordinates, times, and noisy temperatures. A JSON file specifies the
mesh, forcing, boundary conditions, and noise standard deviation. The hidden reference coefficient is
available only to the verifier.

### Computation resources (time and device)
CPU only, four cores, 8 GiB memory, and a 20-minute agent time limit.

### Expected workflow & outputs
Inspect the forward model, estimate a smooth positive coefficient field, and write a CSV file with one
coefficient value per grid cell. The agent may use numerical optimization but must not modify the supplied
observations or verifier.

## Evaluation

### How will this task be evaluated?
The verifier checks the output shape and positivity, then computes relative L2 error against the hidden
coefficient and forward-prediction error at held-out sensor locations. A full reward requires both errors
below published thresholds; partial reward scales continuously with prediction quality. An oracle solution
and a no-op control would be run before release.

### Is there risk of cheating and leakage?
Training examples use different seeds and fields from the hidden instance. The reference coefficient and
verifier thresholds are kept outside the agent workspace. The verifier recomputes predictions from the
submitted coefficient, so copying a training output cannot pass.

## Contributor

Name: AI4S-Bench live integration test
Institution / affiliation: AI4S-Bench
GitHub: https://github.com/{login}

## Task Metadata
Proposed task slug: `inverse-diffusion-coefficient-live-e2e`

---
Submitted as a live AI review integration test. This public proposal is intentionally labeled as a test and
does not request human acceptance.
"""


def discussion_comments(token: str, number: int) -> list[dict]:
    owner, name = REPOSITORY.split("/", 1)
    query = """query($owner:String!,$name:String!,$number:Int!,$cursor:String) {
      repository(owner:$owner,name:$name) { discussion(number:$number) {
        comments(first:100,after:$cursor) {
          pageInfo { hasNextPage endCursor }
          nodes { url body author { login } }
        }
      } }
    }"""
    cursor = None
    comments: list[dict] = []
    while True:
        data = graphql(token, query, {"owner": owner, "name": name, "number": number, "cursor": cursor})
        page = data["repository"]["discussion"]["comments"]
        comments.extend(page["nodes"])
        if not page["pageInfo"]["hasNextPage"]:
            return comments
        cursor = page["pageInfo"]["endCursor"]


@unittest.skipUnless(
    os.environ.get("AI_REVIEW_LIVE") == "1", "Set AI_REVIEW_LIVE=1 to publish a real proposal"
)
class LiveAIReviewE2E(unittest.TestCase):
    def test_proposal_reaches_deployed_ai_review(self) -> None:
        token = github_token()
        health = request_json(BACKEND_URL + "/health/ready")
        self.assertEqual(health.get("status"), "ready")
        schema = request_json(BACKEND_URL + "/openapi.json")
        self.assertIn("/api/v1/internal/proposal-ai-reviews", schema["paths"])

        variable = request_json(
            f"https://api.github.com/repos/{REPOSITORY}/actions/variables/AI_REVIEW_BACKEND_URL",
            token=token,
        )
        self.assertEqual(variable["value"].rstrip("/"), BACKEND_URL)
        workflow = request_json(
            f"https://api.github.com/repos/{REPOSITORY}/actions/workflows/{WORKFLOW}", token=token
        )
        self.assertEqual(workflow["state"], "active")

        data = graphql(
            token,
            """query {
          viewer { login }
          repository(owner:"AI4S-Bench",name:"ai4s-benchmark") {
            id discussionCategories(first:50) { nodes { id name } }
          }
        }""",
        )
        login = data["viewer"]["login"]
        repository = data["repository"]
        category = next(
            item for item in repository["discussionCategories"]["nodes"] if item["name"] == "Task Proposals"
        )

        resumed = os.environ.get("AI_REVIEW_E2E_DISCUSSION_NUMBER")
        if resumed:
            number = int(resumed)
            discussion = graphql(
                token,
                """query($number:Int!) {
              repository(owner:"AI4S-Bench",name:"ai4s-benchmark") {
                discussion(number:$number) { id number title url author { login } }
              }
            }""",
                {"number": number},
            )["repository"]["discussion"]
            self.assertTrue(discussion["title"].startswith("AI review E2E:"))
            self.assertEqual(discussion["author"]["login"].lower(), login.lower())
        else:
            title = "AI review E2E: inverse diffusion coefficient " + datetime.now(UTC).strftime(
                "%Y%m%d-%H%M%S UTC"
            )
            created = graphql(
                token,
                """mutation($input:CreateDiscussionInput!) {
              createDiscussion(input:$input) { discussion { id number title url } }
            }""",
                {
                    "input": {
                        "repositoryId": repository["id"],
                        "categoryId": category["id"],
                        "title": title,
                        "body": proposal_body(login),
                    }
                },
            )
            discussion = created["createDiscussion"]["discussion"]
            number = discussion["number"]
        action = "Monitoring" if resumed else "Published"
        print(f"\n{action} proposal: {discussion['url']}", flush=True)
        print(f"Resume with AI_REVIEW_E2E_DISCUSSION_NUMBER={number}", flush=True)

        timeout = int(os.environ.get("AI_REVIEW_E2E_TIMEOUT_SECONDS", "1200"))
        deadline = time.monotonic() + timeout
        comment = None
        recommendation = None
        unavailable_reported = False
        while time.monotonic() < deadline:
            comment = next(
                (item for item in discussion_comments(token, number) if MARKER in item["body"]), None
            )
            if comment:
                recommendation = re.search(r"(?m)^\*\*Recommendation:\*\* (.+)$", comment["body"])
                if recommendation and recommendation.group(1).strip() in DECISIONS:
                    break
                if not unavailable_reported:
                    print(f"AI review is not yet complete: {comment['url']}", flush=True)
                    unavailable_reported = True
            time.sleep(15)
        self.assertIsNotNone(comment, f"No AI review comment appeared within {timeout}s: {discussion['url']}")
        self.assertIsNotNone(recommendation, "AI review comment has no recommendation")
        self.assertIn(
            recommendation.group(1).strip(),
            DECISIONS,
            f"No completed AI review appeared within {timeout}s: {comment['url']}",
        )
        print(f"AI review comment: {comment['url']}", flush=True)
        self.assertIn("<details><summary>Full review</summary>", comment["body"])
        run_match = re.search(r"/actions/runs/(\d+)/attempts/(\d+)", comment["body"])
        self.assertIsNotNone(run_match, "AI review comment has no Actions run link")
        run_id = run_match.group(1)
        while time.monotonic() < deadline:
            run = request_json(
                f"https://api.github.com/repos/{REPOSITORY}/actions/runs/{run_id}", token=token
            )
            if run["status"] == "completed":
                self.assertEqual(run["conclusion"], "success", run["html_url"])
                break
            time.sleep(10)
        else:
            self.fail(f"AI review workflow did not complete within {timeout}s: {run['html_url']}")
        print(f"AI review succeeded: {recommendation.group(1).strip()} ({run['html_url']})", flush=True)


if __name__ == "__main__":
    unittest.main()
