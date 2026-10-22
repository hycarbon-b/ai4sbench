# Website preview changes

When changing the Website, update the `ai4s-bench-website` submodule pointer and
run `python scripts/sync_website_preview.py` to refresh the committed
`backend/src/website_dist/` snapshot. Review both in the parent PR. The preview
may use the latest commit pushed to the configured Website fork; its source PR
does not need to merge into the official Website repository or update the
official GitHub Pages site first. The backend serves the snapshot at
`/website-preview/`; production deploys only after the parent PR is merged to
`main`.
