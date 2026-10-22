# Project information

## Identity

- **Project:** AI4S-Bench Control Panel and Website backend
- **Repository:** `hycarbon-b/ai4sbench`
- **Purpose:** Operate the AI4S-Bench contribution workflow, proposal and review
  APIs, operator Dashboard, persistence, notifications, and benchmark execution.
- **Production target:** The EC2 service deploys parent-repository `main`.
- **Public project site:** `https://ai4sbench.org/` (published separately from
  the backend-hosted Website preview).

## Architecture and key paths

- `backend/`: FastAPI service, database models and migrations, API contracts,
  worker jobs, static Dashboard assets, and the Website preview snapshot.
- `dashboard-frontend/frontend/`: React Dashboard source; built files are
  committed under `backend/src/static/`.
- `ai4s-bench-website/`: Website source, included as a Git submodule.
- `benchmark-repository/`: Benchmark task definitions, included as a Git
  submodule.
- `reference/`: Reference material and selected upstream projects; not the
  application runtime.
- `scripts/`: Repository maintenance and deployment helper scripts.
- `EC2_RUNBOOK.md`: CI, release, and production deployment procedures.

## Stack and operating commands

- **Backend:** Python 3.12+, FastAPI, SQLAlchemy, Alembic, and `uv`.
- **Dashboard:** React, TypeScript, Vite, Node.js, and npm.
- **Local API:** `uv run ai4sbench-api` from `backend/`; default local routes
  include `/` for the Dashboard and `/website-preview/` for the Website.
- **Repository CI:** `python scripts/ci.py` from the repository root.
- **Database schema:** Update `backend/migrations/versions/` whenever a
  persistent schema change requires migration; keep existing database upgrades
  supported.

## Change and release expectations

- Keep generated Dashboard assets in `backend/src/static/` in sync with their
  frontend source.
- Update relevant API, architecture, and runbook documentation with behavior
  changes.
- Production deploys parent `main`. A feature branch or an unmerged parent PR
  is not a production deployment target.
- Read `EC2_RUNBOOK.md` before changing or operating the production deployment.
- After parent-main CI passes, deploy over SSH by running
  `sudo bash /opt/ai4sbench/deploy/deploy.sh <sha>` on EC2. `deploy/` contains
  server-side operations only. Update it and the runbook together when the
  deployment procedure changes.

# Submodule management

The parent repository records each submodule as a pinned Git commit (a gitlink).
The submodule's files and its pointer are committed separately: commit and push
source changes in the submodule repository, then stage the new gitlink in the
parent repository.

## General workflow

- Clone with `git clone --recurse-submodules ...`; for an existing checkout use
  `git submodule update --init --recursive`.
- Inspect configured paths and remotes in `.gitmodules`; use
  `git submodule status --recursive` to inspect the commits currently pinned by
  the parent.
- Make source edits and commits inside the relevant submodule. Push the commit
  to a branch reachable through that submodule's configured remote before
  updating the parent pointer.
- In the parent checkout, select the intended submodule commit and run
  `git add <submodule-path>`. Review the gitlink change with
  `git diff --cached --submodule=log` before opening the parent PR.
- Do not copy or commit a submodule's `.git` metadata into the parent. Do not
  change a submodule URL in `.gitmodules` unless the source of record is
  intentionally changing.
- For a source PR that is still open, pin only a commit already pushed to its
  fork or branch. Document that the parent PR is consuming that commit.

## Website submodule and backend preview

`ai4s-bench-website/` is configured to use the `hycarbon-b/ai4s-bench-website`
fork. The backend serves the committed static snapshot in
`backend/src/website_dist/` at `/website-preview/`; it does not read the
submodule checkout at runtime.

To prepare a preview update:

1. Commit and push the intended Website source commit to the configured fork.
   The Website source PR may remain open. It does not need to merge into the
   official Website repository, and the official GitHub Pages site does not
   need to update first.
2. Check out that pushed commit in `ai4s-bench-website/` and stage the gitlink:

   ```bash
   git -C ai4s-bench-website fetch origin
   git -C ai4s-bench-website checkout <website-commit>
   git add ai4s-bench-website
   ```

3. Mirror and verify the preview snapshot, then stage it:

   ```bash
   python scripts/sync_website_preview.py
   python scripts/sync_website_preview.py --check
   git add backend/src/website_dist
   ```

   The sync script checks that the staged gitlink matches the checked-out
   Website commit, copies site files and assets (excluding repository-only
   content such as tests and `.github/`), and removes stale snapshot files.
4. Review both the submodule pointer and snapshot in the parent PR. Merge that
   PR to `main` after CI passes; the production runbook then deploys the
   committed snapshot.

The backend preview release and the official GitHub Pages release are separate.
A later merge of the Website source PR does not refresh the backend snapshot;
update the submodule pointer and snapshot through another parent PR when needed.
See `EC2_RUNBOOK.md` under **Backend-hosted Website preview** for the release
and verification steps.
