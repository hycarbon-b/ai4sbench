# ai4sbench

ai4sbench is a contribution and execution system for scientific Harbor benchmarks.
It separates the public task repository from the private control plane and its
operator dashboard.

```text
ai4sbench/
├── backend/                 FastAPI control plane, SQLite, EC2 worker lifecycle
├── dashboard-frontend/      React operator and contribution dashboard
└── benchmark-repository/    Public Harbor task repository and contribution checks
```

## Components

- [`backend/`](backend/README.md) owns GitHub OAuth, Proposal-to-Discussion
  submission, immutable task revision records, SQLite job dispatch, EC2 worker
  lifecycle, migrations, deployment configuration, and private environment data.
- [`dashboard-frontend/frontend/`](dashboard-frontend/frontend/README.md)
  contains the React application that is built into the backend's static assets.
- [`benchmark-repository/`](benchmark-repository/README.md) is structured as a
  standalone Terminal-Bench-Science-style repository. It contains only public
  task material, benchmark maintenance tools, and contribution validation.

## Local development

Run the control plane from `backend/`; build the dashboard from
`dashboard-frontend/frontend/`; maintain and validate tasks from
`benchmark-repository/`. Each component documents its own commands and
boundaries. Docker Compose is invoked with:

```bash
docker compose -f backend/compose.yaml up --build
```

The root deliberately contains only the project overview and repository-level
Git configuration. Credentials remain ignored under `backend/.env` and are not
part of the public benchmark repository.
