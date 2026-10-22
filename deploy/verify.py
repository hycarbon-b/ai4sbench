"""Read-only checks run on EC2 after the deployment service restart."""

import hashlib
import json
import re
import sys
from pathlib import Path
from urllib.request import Request, urlopen


def fetch(url):
    request = Request(
        url,
        headers={
            "User-Agent": "AI4SBench-Deploy/1.0",
            "Cache-Control": "no-cache",
            "Accept-Encoding": "identity",
        },
    )
    with urlopen(request, timeout=15) as response:
        return response.read()


def verify(repo, base, public=False):
    assert json.loads(fetch(base + "/health/ready"))["status"] == "ready"
    paths = json.loads(fetch(base + "/openapi.json"))["paths"]
    assert "/api/v1/public/proposals" in paths, "Public proposal route missing"
    assert "/api/v1/proposals/{proposal_id}/reviews" in paths, "Review submission route missing"
    proposals = json.loads(fetch(base + "/api/v1/public/proposals"))["items"]
    assert isinstance(proposals, list), "Unexpected proposal response"
    count = 0
    for directory, prefix in (("static", ""), ("website_dist", "/website-preview")):
        root = repo / "backend/src" / directory
        assert (root / "index.html").is_file(), f"Missing {root}/index.html"
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            if path.name == "index.html":
                relative = relative.removesuffix("index.html")
            url = base + prefix + "/" + relative
            actual, expected = fetch(url), path.read_bytes()
            if public and path.suffix == ".html":
                # Cloudflare injects analytics and rewrites email addresses in HTML.
                # Check resource references publicly; hash complete HTML on loopback.
                html = expected.decode("utf-8")
                references = re.findall(r"<(?:script|link)\b[^>]*\b(?:src|href)=[\"\']([^\"\']+)", html)
                title = re.search(r"<title>.*?</title>", html, re.DOTALL)
                markers = references + ([title.group()] if title else [])
                assert all(marker.encode() in actual for marker in markers), f"Stale HTML: {url}"
            else:
                assert hashlib.sha256(actual).digest() == hashlib.sha256(expected).digest(), (
                    f"Published asset differs: {url}"
                )
            count += 1
    print(f"Verified {base}: API, {len(proposals)} proposals, {count} Dashboard/Website assets")


if __name__ == "__main__":
    checkout = Path(sys.argv[1])
    verify(checkout, "http://127.0.0.1:8080")
    verify(checkout, sys.argv[2].rstrip("/"), public=True)
