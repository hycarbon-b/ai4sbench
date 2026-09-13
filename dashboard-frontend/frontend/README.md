# ai4sbench dashboard frontend

React and shadcn-style component source for the contribution portal and control
plane dashboard.

For local UI development, start Vite and keep the backend running separately:

```bash
npm ci
npm run dev
```

The browser origin used by Vite must be present in the backend's
`TBCP_CORS_ORIGINS` when it calls a different backend origin. GitHub sign-in
must still begin at the backend `/auth/github/start` route so the OAuth state
cookie is created on the callback origin.

Before committing or deploying a Dashboard change, run:

```bash
npm ci
npm run check
npm run build
```

The production build writes directly to
`../../backend/control_panel/static/`, where FastAPI serves it. Commit the
generated `index.html` and hashed assets with the React source; production
receives them through Git rather than a direct file upload.

The Proposal table is administrator-only. Its Delete action sets a backend
`deleted_at` tombstone: it does not delete the GitHub Discussion, and a later
Full Sync will continue to skip that Discussion.

No credentials, cloud configuration, OAuth tokens, or worker tokens belong in
this directory or its generated assets.
