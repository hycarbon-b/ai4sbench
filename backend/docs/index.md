# Backend Developer Wiki

This site is the developer-facing map of the AI4S-Bench backend. Treat the
implementation and migrations as the ground truth for behavior; use these pages
to find the relevant code, contracts, and operational constraints. Update the
wiki in the same change when a behavior or interface changes.

## Start here

- [Architecture](architecture.md): application composition, domain boundaries,
  persistence, background work, and API contract locations.
- [Runtime and Configuration](runtime-configuration.md): local startup,
  environment settings, runtime processes, and deployment notes.
- [AI Proposal Reviews](ai-proposal-reviews.md): the benchmark repository's
  automated proposal review integration.

## Ground-truth sources

| Concern | Source of truth |
| --- | --- |
| HTTP routes and request/response models | `src/**/routes.py`, `src/**/schemas.py`, and generated OpenAPI schemas |
| Database schema and upgrades | `src/db/` and `migrations/versions/` |
| Runtime configuration | `src/core/config.py` and `.env.example` |
| Execution and queue behavior | `src/control_panel/`, `src/jobs/`, and `src/community/` |
| Automated behavior checks | `tests/` |

For setup commands and the broader repository layout, see `backend/README.md`.
