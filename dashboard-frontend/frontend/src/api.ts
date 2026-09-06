export type User = {
  id: string;
  email: string;
  github_login: string;
  role: "admin" | "contributor" | "member";
};

export type ProposalInput = {
  title: string;
  domain: string;
  field_name: string;
  problem: string;
  solvability: string;
  references: string;
  software: string;
  dataset: string;
  compute: string;
  workflow: string;
  evaluation: string;
  leakage: string;
  name: string;
  affiliation: string;
  github: string;
};

export type Proposal = {
  id: string;
  title: string;
  domain: string;
  field: string;
  task_slug: string;
  status: string;
  discussion_url: string;
  author_login: string;
  discussion_number?: number | null;
  input_valid: boolean;
};

export type TaskRevision = {
  id: string;
  repo_url: string;
  commit_sha: string;
  task_path: string;
  resource_requirements: Record<string, number>;
  created_at: string;
};

export type Plan = {
  id: string;
  task_revision_id: string;
  state: string;
  config: Record<string, unknown>;
  lock_version: number;
  approved_by: string | null;
  approved_at: string | null;
  created_at: string;
  repo_url: string;
  commit_sha: string;
  task_path: string;
};

export type Run = {
  id: string;
  plan_id: string;
  state: string;
  config: Record<string, unknown>;
  deadline_at: string;
  instance_id: string | null;
  instance_state: string | null;
  result: Record<string, unknown>;
  version: number;
  created_at: string;
  updated_at: string;
  repo_url: string;
  commit_sha: string;
  task_path: string;
};

export type Job = {
  id: string;
  kind: string;
  state: string;
  attempts: number;
  max_attempts: number;
  available_at: string;
  lease_owner: string | null;
  lease_expires_at: string | null;
  last_error: string | null;
};

export type CloudProfile = {
  id: string;
  name: string;
  provider: string;
  allocation: Record<string, unknown>;
  enabled: boolean;
};

export type DatabaseSnapshot = {
  name: string;
  size_bytes: number;
  created_at: string;
};

export type Dashboard = {
  counts: Record<string, number>;
  runs: Run[];
  plans: Plan[];
  task_revisions: TaskRevision[];
};

export async function api<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await fetch(path, {
    credentials: "same-origin",
    ...options,
    headers: { "Content-Type": "application/json", ...options.headers },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(
      typeof body.detail === "string"
        ? body.detail
        : `Request failed (${response.status})`,
    );
  }
  return response.status === 204
    ? (undefined as T)
    : (response.json() as Promise<T>);
}

export const getCurrentUser = () => api<User>("/api/v1/auth/me");
export const getGithubAuthorizeUrl = () =>
  api<{ authorization_url: string }>("/auth/github/authorize");
export const createProposal = (input: ProposalInput) =>
  api<Proposal>("/api/v1/proposals", {
    method: "POST",
    body: JSON.stringify(input),
  });
export const syncProposalDiscussions = () =>
  api<{
    scanned_count: number;
    created_count: number;
    updated_count: number;
    invalid_count: number;
  }>("/api/v1/proposals/sync-discussions", { method: "POST" });
export const signOut = () =>
  api<void>("/api/v1/auth/logout", { method: "POST" });
export const listDatabaseSnapshots = () =>
  api<{ items: DatabaseSnapshot[] }>("/api/v1/database-snapshots");
export const createDatabaseSnapshot = () =>
  api<DatabaseSnapshot>("/api/v1/database-snapshots", { method: "POST" });
export const databaseSnapshotDownloadUrl = (name: string) =>
  `/api/v1/database-snapshots/${encodeURIComponent(name)}/download`;
