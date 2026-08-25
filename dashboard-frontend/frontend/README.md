# ai4sbench dashboard frontend

React and shadcn-style component source for the contribution portal and control
plane dashboard.

```bash
npm ci
npm run check
npm run build
```

The production build writes directly to
`../../backend/control_panel/static/`, where FastAPI serves it. No credentials,
cloud configuration, or worker tokens belong in this directory.
