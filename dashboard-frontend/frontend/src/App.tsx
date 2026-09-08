import * as Avatar from "@radix-ui/react-avatar";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import {
  Activity,
  CheckCircle2,
  CloudCog,
  Database,
  Download,
  ExternalLink,
  Github,
  History,
  LoaderCircle,
  LogOut,
  Play,
  RefreshCw,
  Rocket,
  Send,
  ServerCog,
  SquareStack,
  StopCircle,
  UploadCloud,
  Webhook,
} from "lucide-react";
import { FormEvent, ReactNode, useEffect, useMemo, useState } from "react";

import {
  api,
  createDatabaseSnapshot,
  createProposal,
  databaseSnapshotDownloadUrl,
  getCurrentUser,
  getGithubAuthorizeUrl,
  getProposalDomains,
  resendWebhookDelivery,
  signOut,
  syncProposalDiscussions,
  type CloudProfile,
  type DatabaseSnapshot,
  type Dashboard,
  type Job,
  type Plan,
  type Proposal,
  type ProposalInput,
  type Run,
  type TaskRevision,
  type User,
  type WebhookDelivery,
} from "./api";
import { Badge } from "./components/ui/badge";
import { Button } from "./components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "./components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "./components/ui/dialog";
import { Input } from "./components/ui/input";
import { Label } from "./components/ui/label";
import { Textarea } from "./components/ui/textarea";
import {
  Table as UiTable,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "./components/ui/table";

type View =
  | "overview"
  | "tasks"
  | "plans"
  | "runs"
  | "jobs"
  | "webhooks"
  | "cloud"
  | "snapshots"
  | "proposals";
type Config = {
  agent: string;
  model: string;
  n_concurrent: number;
  n_attempts: number;
  environment: "docker";
  instance_type: string;
  root_volume_gb: number;
};
type Ops = {
  dashboard: Dashboard | null;
  proposals: Proposal[];
  tasks: TaskRevision[];
  plans: Plan[];
  runs: Run[];
  jobs: Job[];
  webhooks: WebhookDelivery[];
  profiles: CloudProfile[];
  snapshots: DatabaseSnapshot[];
};

const emptyOps: Ops = {
  dashboard: null,
  proposals: [],
  tasks: [],
  plans: [],
  runs: [],
  jobs: [],
  webhooks: [],
  profiles: [],
  snapshots: [],
};
const emptyProposal: ProposalInput = {
  title: "",
  domain: "",
  field_name: "",
  problem: "",
  solvability: "",
  references: "",
  software: "",
  dataset: "",
  compute: "",
  workflow: "",
  evaluation: "",
  leakage: "",
  name: "",
  affiliation: "",
  github: "",
};
const sampleProposal: ProposalInput = {
  title: "Adaptive mesh refinement for a discontinuous Poisson problem",
  domain: "Applied Mathematics",
  field_name: "Numerical partial differential equations",
  problem:
    "Construct and validate an adaptive finite-element workflow for a two-dimensional Poisson equation with discontinuous material coefficients. The submitted solution must resolve the interface, use an error estimator to drive refinement, and demonstrate convergence against a fixed manufactured reference problem.",
  solvability:
    "The mathematical model, boundary conditions, and verifier target are specified well enough for an expert to solve without inventing missing scientific assumptions.",
  references:
    "FEniCSx documentation; Verfurth, A Review of A Posteriori Error Estimation; and the repository's generated manufactured-solution protocol and provenance script.",
  software:
    "Linux container with Python 3.11 and FEniCSx-compatible finite-element tooling.",
  dataset:
    "The task uses a synthetic manufactured-solution dataset generated in the repository. It is CC0, approximately 25 MiB, fully visible to the agent, and paired with a held-out mesh-resolution configuration used only by the verifier.",
  compute:
    "Four vCPUs, 8 GiB memory, 20 GiB temporary storage, and a 45-minute timeout.",
  workflow:
    "The task provides a containerized solver skeleton, coefficient field, boundary conditions, and a coarse initial mesh. The contributor implements the mesh-refinement loop, writes solver outputs to the prescribed directory, and records error estimates and mesh statistics in a machine-readable summary.",
  evaluation:
    "A deterministic verifier reruns the solver on visible and held-out mesh configurations. It checks output schema, residual reduction, interface-aware error norms, refinement efficiency, and reproducibility from a clean container. A no-op baseline must fail the accuracy threshold.",
  leakage:
    "Held-out mesh configurations remain unavailable to the agent, and the verifier rejects copied reference outputs.",
  name: "Example Scientist",
  affiliation: "Example University",
  github: "scientist",
};
const initialConfig: Config = {
  agent: "codex",
  model: "gpt-5.2-codex",
  n_concurrent: 1,
  n_attempts: 1,
  environment: "docker",
  instance_type: "",
  root_volume_gb: 30,
};

const short = (value: string) => value.slice(0, 8);
const bytes = (value: number) => {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MiB`;
};
const when = (value?: string | null) =>
  value
    ? new Intl.DateTimeFormat(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(new Date(value))
    : "—";
const ready = (value: ProposalInput) =>
  value.title.length >= 12 &&
  value.domain.trim().length >= 2 &&
  value.field_name.trim().length >= 2 &&
  value.problem.length >= 80 &&
  value.solvability.length >= 20 &&
  value.references.length >= 40 &&
  value.software.length >= 10 &&
  value.dataset.length >= 10 &&
  value.compute.length >= 5 &&
  value.workflow.length >= 20 &&
  value.evaluation.length >= 20 &&
  value.leakage.length >= 10 &&
  value.name.trim().length >= 2 &&
  /^[a-z\d](?:[a-z\d]|-(?=[a-z\d])){0,38}$/i.test(value.github);

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [checked, setChecked] = useState(false);
  const [view, setView] = useState<View>("proposals");
  const [loginOpen, setLoginOpen] = useState(false);
  const [loginPending, setLoginPending] = useState(false);
  const [ops, setOps] = useState<Ops>(emptyOps);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [repo, setRepo] = useState(
    "https://github.com/harbor-framework/terminal-bench-science.git",
  );
  const [ref, setRef] = useState("main");
  const [selected, setSelected] = useState<string[]>([]);
  const [config, setConfig] = useState<Config>(initialConfig);
  const [timeoutMinutes, setTimeoutMinutes] = useState(180);
  const [runEvents, setRunEvents] = useState<
    Array<{
      id: string;
      event_type: string;
      message: string;
      created_at: string;
    }>
  >([]);
  const [proposal, setProposal] = useState(emptyProposal);
  const [proposalDomains, setProposalDomains] = useState<string[]>([]);
  const [proposalResult, setProposalResult] = useState<Proposal | null>(null);
  const [profileName, setProfileName] = useState("");
  const [allocation, setAllocation] = useState(
    '{"region":"us-east-1","max_active_runs":1,"allowed_instance_types":["t3.micro"]}',
  );

  const isAdmin = user?.role === "admin";
  const proposalForSubmission = useMemo(
    () => ({ ...proposal, github: user?.github_login || proposal.github }),
    [proposal, user],
  );
  const canSubmitProposal = useMemo(
    () => ready(proposalForSubmission),
    [proposalForSubmission],
  );

  const loadOps = async () => {
    if (!isAdmin) return;
    setLoading(true);
    setError("");
    try {
      const [dashboard, proposals, tasks, plans, runs, jobs, webhooks, profiles, snapshots] =
        await Promise.all([
          api<Dashboard>("/api/v1/dashboard"),
          api<{ items: Proposal[] }>("/api/v1/proposals"),
          api<{ items: TaskRevision[] }>("/api/v1/task-revisions"),
          api<{ items: Plan[] }>("/api/v1/plans"),
          api<{ items: Run[] }>("/api/v1/runs"),
          api<{ items: Job[] }>("/api/v1/jobs"),
          api<{ items: WebhookDelivery[] }>("/api/v1/webhook-deliveries"),
          api<{ items: CloudProfile[] }>("/api/v1/cloud-profiles"),
          api<{ items: DatabaseSnapshot[] }>("/api/v1/database-snapshots"),
        ]);
      setOps({
        dashboard,
        proposals: proposals.items,
        tasks: tasks.items,
        plans: plans.items,
        runs: runs.items,
        jobs: jobs.items,
        webhooks: webhooks.items,
        profiles: profiles.items,
        snapshots: snapshots.items,
      });
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Could not load the control-plane data.",
      );
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void (async () => {
      try {
        const next = await getCurrentUser();
        setUser(next);
        setView("proposals");
      } catch {
        setUser(null);
      } finally {
        setChecked(true);
      }
    })();
  }, []);
  useEffect(() => {
    void getProposalDomains()
      .then(({ items }) => setProposalDomains(items))
      .catch(() => setError("Could not load the shared proposal domain options."));
  }, []);
  useEffect(() => {
    void loadOps();
  }, [isAdmin]);

  const login = async () => {
    setLoginPending(true);
    setError("");
    try {
      const { authorization_url } = await getGithubAuthorizeUrl();
      const popup = window.open(
        authorization_url,
        "ai4sbench-github",
        "popup,width=700,height=780",
      );
      if (!popup) throw new Error("Allow popups to continue with GitHub.");
      const poll = window.setInterval(async () => {
        try {
          const next = await getCurrentUser();
          setUser(next);
          setLoginOpen(false);
          setLoginPending(false);
          popup.close();
          window.clearInterval(poll);
        } catch {
          if (popup.closed) {
            setLoginPending(false);
            window.clearInterval(poll);
          }
        }
      }, 800);
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "GitHub sign-in could not start.",
      );
      setLoginPending(false);
    }
  };

  const sync = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError("");
    try {
      const result = await api<{
        created_count: number;
        updated_count: number;
        commit_sha: string;
      }>("/api/v1/task-revisions/sync-repository", {
        method: "POST",
        body: JSON.stringify({ repo_url: repo, ref }),
      });
      setMessage(
        `Synced ${result.created_count} new and ${result.updated_count} updated tasks at ${short(result.commit_sha)}.`,
      );
      await loadOps();
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Repository synchronization failed.",
      );
    }
  };

  const launch = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError("");
    if (!selected.length) {
      setError("Select at least one task revision to queue.");
      return;
    }
    const launchConfig = {
      ...config,
      instance_type: config.instance_type || null,
    };
    const key = globalThis.crypto?.randomUUID?.() || `console-${Date.now()}`;
    try {
      if (selected.length === 1)
        await api<Run>("/api/v1/runs/manual", {
          method: "POST",
          headers: { "Idempotency-Key": key },
          body: JSON.stringify({
            task_revision_id: selected[0],
            config: launchConfig,
            timeout_minutes: timeoutMinutes,
          }),
        });
      else
        await api<{ created_count: number }>("/api/v1/runs/manual-batch", {
          method: "POST",
          headers: { "Idempotency-Key": key },
          body: JSON.stringify({
            task_revision_ids: selected,
            config: launchConfig,
            timeout_minutes: timeoutMinutes,
          }),
        });
      setMessage(
        `${selected.length} Harbor run${selected.length === 1 ? "" : "s"} queued.`,
      );
      setView("runs");
      await loadOps();
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Could not queue these runs.",
      );
    }
  };

  const cancel = async (runId: string) => {
    try {
      await api<Run>(`/api/v1/runs/${runId}/cancel`, { method: "POST" });
      setMessage(`Run ${short(runId)} is being cancelled.`);
      await loadOps();
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "Could not cancel this run.",
      );
    }
  };
  const events = async (runId: string) => {
    try {
      setRunEvents(
        (await api<{ items: typeof runEvents }>(`/api/v1/runs/${runId}/events`))
          .items,
      );
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "Could not load run events.",
      );
    }
  };
  const saveProfile = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError("");
    try {
      await api<CloudProfile>("/api/v1/cloud-profiles", {
        method: "POST",
        body: JSON.stringify({
          name: profileName,
          provider: "aws",
          allocation: JSON.parse(allocation),
          enabled: true,
        }),
      });
      setProfileName("");
      setMessage(
        "Cloud allocation profile saved. Credentials remain server-side.",
      );
      await loadOps();
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Could not save the cloud profile.",
      );
    }
  };
  const saveDatabaseSnapshot = async () => {
    setLoading(true);
    setError("");
    try {
      const snapshot = await createDatabaseSnapshot();
      setMessage(`SQLite snapshot ${snapshot.name} saved to the server cache.`);
      await loadOps();
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Could not save the SQLite snapshot.",
      );
    } finally {
      setLoading(false);
    }
  };
  const resendWebhook = async (delivery: WebhookDelivery) => {
    if (
      delivery.state === "completed" &&
      !window.confirm(
        "This delivery already completed. Send the same Discord notification again?",
      )
    )
      return;
    setLoading(true);
    setError("");
    try {
      await resendWebhookDelivery(delivery.id);
      setMessage(`Webhook delivery ${short(delivery.id)} queued again.`);
      await loadOps();
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Could not queue the webhook delivery again.",
      );
    } finally {
      setLoading(false);
    }
  };
  const submitProposal = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError("");
    if (!user) {
      setLoginOpen(true);
      return;
    }
    if (!canSubmitProposal) {
      setError("Complete every proposal field using the expected format.");
      return;
    }
    try {
      const created = await createProposal(proposalForSubmission);
      setProposalResult(created);
      setMessage("Proposal submitted and GitHub Discussion opened.");
      if (isAdmin) await loadOps();
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "The proposal could not be submitted.",
      );
    }
  };
  const syncDiscussions = async () => {
    setLoading(true);
    setError("");
    try {
      const result = await syncProposalDiscussions();
      setMessage(
        `Scanned ${result.scanned_count} Discussions; added ${result.created_count}, updated ${result.updated_count}; ${result.invalid_count} need the new form fields.`,
      );
      await loadOps();
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Discussion reconciliation failed.",
      );
    } finally {
      setLoading(false);
    }
  };
  const logout = async () => {
    await signOut();
    setUser(null);
    setView("proposals");
    setOps(emptyOps);
  };
  const choose = (id: string, checked: boolean) =>
    setSelected((items) =>
      checked
        ? [...new Set([...items, id])]
        : items.filter((item) => item !== id),
    );

  return (
    <div className="min-h-screen bg-[#0b0f14] text-slate-100">
      <header className="sticky top-0 z-30 border-b border-slate-800 bg-[#0b0f14]">
        <div className="mx-auto flex h-12 max-w-[96rem] items-center justify-between px-4">
          <button
            onClick={() => setView(isAdmin ? "overview" : "proposals")}
            className="flex items-center gap-2 font-medium tracking-tight"
          >
            <span className="grid size-6 place-items-center rounded-sm bg-sky-400 text-slate-950">
              <SquareStack className="size-4" />
            </span>
            ai4sbench{" "}
            <span className="hidden text-[11px] font-normal text-slate-500 sm:inline">
              / control plane
            </span>
          </button>
          <div className="flex items-center gap-2">
            {isAdmin && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => void loadOps()}
                disabled={loading}
              >
                <RefreshCw
                  className={`size-3 ${loading ? "animate-spin" : ""}`}
                />
                Refresh
              </Button>
            )}
            {user ? (
              <UserMenu user={user} onLogout={() => void logout()} />
            ) : (
              <Button
                variant="outline"
                size="sm"
                onClick={() => setLoginOpen(true)}
              >
                <Github className="size-3" />
                Sign in
              </Button>
            )}
          </div>
        </div>
      </header>
      <main className="mx-auto grid max-w-[96rem] gap-4 px-4 py-4 lg:grid-cols-[13rem_1fr]">
        <Sidebar active={view} admin={isAdmin} onChange={setView} />
        <div className="min-w-0 space-y-4">
          {(message || error) && (
            <Notice
              error={error}
              message={message}
              onClose={() => {
                setMessage("");
                setError("");
              }}
            />
          )}
          {isAdmin ? (
            <AdminPanel
              view={view}
              ops={ops}
              selected={selected}
              config={config}
              timeoutMinutes={timeoutMinutes}
              runEvents={runEvents}
              repo={repo}
              ref={ref}
              profileName={profileName}
              allocation={allocation}
              onRepo={setRepo}
              onRef={setRef}
              onSync={sync}
              onSelect={choose}
              onConfig={setConfig}
              onTimeout={setTimeoutMinutes}
              onLaunch={launch}
              onCancel={cancel}
              onEvents={events}
              onName={setProfileName}
              onAllocation={setAllocation}
              onProfile={saveProfile}
              onSaveDatabaseSnapshot={saveDatabaseSnapshot}
              proposal={proposal}
              proposalDomains={proposalDomains}
              onProposalChange={setProposal}
              proposalResult={proposalResult}
              proposalReady={canSubmitProposal}
              onProposal={submitProposal}
              onSyncDiscussions={syncDiscussions}
              onResendWebhook={resendWebhook}
              loading={loading}
            />
          ) : (
            <ProposalPanel
              proposal={proposal}
              proposalDomains={proposalDomains}
              onChange={setProposal}
              result={proposalResult}
              ready={canSubmitProposal}
              onSubmit={submitProposal}
            />
          )}
        </div>
      </main>
      <Dialog open={loginOpen} onOpenChange={setLoginOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-xl">
              <Github className="size-5" />
              Continue with GitHub
            </DialogTitle>
            <DialogDescription>
              GitHub OAuth is completed by the backend. The browser never stores
              its access token.
            </DialogDescription>
          </DialogHeader>
          <Button
            size="lg"
            onClick={() => void login()}
            disabled={loginPending}
          >
            {loginPending ? (
              <LoaderCircle className="size-4 animate-spin" />
            ) : (
              <Github className="size-4" />
            )}
            {loginPending ? "Waiting for GitHub..." : "Sign in with GitHub"}
          </Button>
        </DialogContent>
      </Dialog>
      {!checked && (
        <div className="fixed bottom-4 right-4 flex items-center gap-2 rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-300">
          <LoaderCircle className="size-4 animate-spin" />
          Checking session
        </div>
      )}
    </div>
  );
}

function AdminPanel({
  view,
  ops,
  selected,
  config,
  timeoutMinutes,
  runEvents,
  repo,
  ref,
  profileName,
  allocation,
  onRepo,
  onRef,
  onSync,
  onSelect,
  onConfig,
  onTimeout,
  onLaunch,
  onCancel,
  onEvents,
  onName,
  onAllocation,
  onProfile,
  proposal,
  proposalDomains,
  onProposalChange,
  proposalResult,
  proposalReady,
  onProposal,
  onSyncDiscussions,
  onResendWebhook,
  onSaveDatabaseSnapshot,
  loading,
}: {
  view: View;
  ops: Ops;
  selected: string[];
  config: Config;
  timeoutMinutes: number;
  runEvents: Array<{
    id: string;
    event_type: string;
    message: string;
    created_at: string;
  }>;
  repo: string;
  ref: string;
  profileName: string;
  allocation: string;
  onRepo: (value: string) => void;
  onRef: (value: string) => void;
  onSync: (event: FormEvent<HTMLFormElement>) => void;
  onSelect: (id: string, checked: boolean) => void;
  onConfig: (value: Config) => void;
  onTimeout: (value: number) => void;
  onLaunch: (event: FormEvent<HTMLFormElement>) => void;
  onCancel: (id: string) => void;
  onEvents: (id: string) => void;
  onName: (value: string) => void;
  onAllocation: (value: string) => void;
  onProfile: (event: FormEvent<HTMLFormElement>) => void;
  proposal: ProposalInput;
  proposalDomains: string[];
  onProposalChange: (value: ProposalInput) => void;
  proposalResult: Proposal | null;
  proposalReady: boolean;
  onProposal: (event: FormEvent<HTMLFormElement>) => void;
  onSyncDiscussions: () => void;
  onResendWebhook: (delivery: WebhookDelivery) => void;
  onSaveDatabaseSnapshot: () => void;
  loading: boolean;
}) {
  if (view === "tasks")
    return (
      <TaskPanel
        tasks={ops.tasks}
        selected={selected}
        config={config}
        timeoutMinutes={timeoutMinutes}
        repo={repo}
        ref={ref}
        onRepo={onRepo}
        onRef={onRef}
        onSync={onSync}
        onSelect={onSelect}
        onConfig={onConfig}
        onTimeout={onTimeout}
        onLaunch={onLaunch}
      />
    );
  if (view === "plans") return <PlansPanel plans={ops.plans} />;
  if (view === "runs")
    return (
      <RunsPanel
        runs={ops.runs}
        events={runEvents}
        onCancel={onCancel}
        onEvents={onEvents}
      />
    );
  if (view === "jobs") return <JobsPanel jobs={ops.jobs} />;
  if (view === "webhooks")
    return (
      <WebhooksPanel
        deliveries={ops.webhooks}
        loading={loading}
        onResend={onResendWebhook}
      />
    );
  if (view === "cloud")
    return (
      <CloudPanel
        profiles={ops.profiles}
        profileName={profileName}
        allocation={allocation}
        onName={onName}
        onAllocation={onAllocation}
        onSubmit={onProfile}
      />
    );
  if (view === "snapshots")
    return (
      <DatabaseSnapshotsPanel
        snapshots={ops.snapshots}
        loading={loading}
        onSave={onSaveDatabaseSnapshot}
      />
    );
  if (view === "proposals")
    return (
      <>
        <div className="flex justify-end">
          <Button
            variant="outline"
            size="sm"
            onClick={onSyncDiscussions}
            disabled={loading}
          >
            <RefreshCw className={`size-3 ${loading ? "animate-spin" : ""}`} />
            Full sync Discussions
          </Button>
        </div>
        <ProposalPanel
          proposal={proposal}
          proposalDomains={proposalDomains}
          onChange={onProposalChange}
          result={proposalResult}
          recent={ops.proposals}
          ready={proposalReady}
          onSubmit={onProposal}
        />
      </>
    );
  const counts = ops.dashboard?.counts || {};
  return (
    <>
      <Title
        eyebrow="Operations"
        title="Harbor run control"
        description="Observe the queue, inspect EC2 workers, and launch only immutable task revisions."
      />
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Metric label="Queued" value={counts.queued || 0} tone="amber" />
        <Metric label="Running" value={counts.running || 0} tone="sky" />
        <Metric
          label="Succeeded"
          value={counts.succeeded || 0}
          tone="emerald"
        />
        <Metric
          label="Failed"
          value={(counts.failed || 0) + (counts.timed_out || 0)}
          tone="rose"
        />
      </div>
      <div className="grid gap-5 xl:grid-cols-[1.15fr_.85fr]">
        <RunsPanel
          runs={ops.runs.slice(0, 6)}
          events={runEvents}
          onCancel={onCancel}
          onEvents={onEvents}
          compact
        />
        <DataCard
          title="Recent execution plans"
          description="The latest immutable plan snapshots."
        >
          {ops.plans.slice(0, 4).map((plan) => (
            <div
              key={plan.id}
              className="mb-3 rounded-lg border border-slate-800 bg-slate-900/50 p-3"
            >
              <div className="flex justify-between gap-2">
                <code className="text-xs text-slate-200">{plan.task_path}</code>
                <Status state={plan.state} />
              </div>
              <p className="mt-2 text-xs text-slate-500">
                {when(plan.created_at)} ·{" "}
                {String(plan.config.instance_type || "automatic")}
              </p>
            </div>
          ))}
          {!ops.plans.length && (
            <Empty text="No execution plans yet. Import a task repository, select a revision, then queue a run." />
          )}
        </DataCard>
      </div>
    </>
  );
}

function TaskPanel({
  tasks,
  selected,
  config,
  timeoutMinutes,
  repo,
  ref,
  onRepo,
  onRef,
  onSync,
  onSelect,
  onConfig,
  onTimeout,
  onLaunch,
}: {
  tasks: TaskRevision[];
  selected: string[];
  config: Config;
  timeoutMinutes: number;
  repo: string;
  ref: string;
  onRepo: (value: string) => void;
  onRef: (value: string) => void;
  onSync: (event: FormEvent<HTMLFormElement>) => void;
  onSelect: (id: string, checked: boolean) => void;
  onConfig: (value: Config) => void;
  onTimeout: (value: number) => void;
  onLaunch: (event: FormEvent<HTMLFormElement>) => void;
}) {
  return (
    <>
      <Title
        eyebrow="Task library"
        title="Immutable Harbor task revisions"
        description="Synchronize a repository branch, review the commit snapshot, then select one or many tasks for a real worker launch."
      />
      <Card>
        <CardContent className="p-5">
          <form
            onSubmit={onSync}
            className="grid gap-4 lg:grid-cols-[1fr_12rem_auto]"
          >
            <Field label="Repository">
              <Input
                value={repo}
                onChange={(event) => onRepo(event.target.value)}
              />
            </Field>
            <Field label="Branch or tag">
              <Input
                value={ref}
                onChange={(event) => onRef(event.target.value)}
              />
            </Field>
            <Button type="submit" className="self-end">
              <UploadCloud className="size-4" />
              Sync latest commit
            </Button>
          </form>
        </CardContent>
      </Card>
      <div className="grid gap-5 xl:grid-cols-[1.35fr_.65fr]">
        <DataCard
          title="Imported tasks"
          description="Selection stays local until Queue Harbor run is submitted."
        >
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr>
                  <th>Run</th>
                  <th>Task</th>
                  <th>Requirements</th>
                  <th>Snapshot</th>
                </tr>
              </thead>
              <tbody>
                {tasks.map((task) => (
                  <tr key={task.id}>
                    <td>
                      <input
                        aria-label={`Select ${task.task_path}`}
                        type="checkbox"
                        checked={selected.includes(task.id)}
                        onChange={(event) =>
                          onSelect(task.id, event.target.checked)
                        }
                      />
                    </td>
                    <td>
                      <code>{task.task_path}</code>
                      <p>{task.repo_url}</p>
                    </td>
                    <td>
                      {Object.entries(task.resource_requirements)
                        .map(([key, value]) => `${key}: ${value}`)
                        .join(" / ") || "Not declared"}
                    </td>
                    <td>{short(task.commit_sha)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!tasks.length && (
              <Empty text="No task revisions are imported. Use the repository sync form above." />
            )}
          </div>
        </DataCard>
        <LaunchPanel
          selected={selected}
          config={config}
          timeoutMinutes={timeoutMinutes}
          onConfig={onConfig}
          onTimeout={onTimeout}
          onLaunch={onLaunch}
        />
      </div>
    </>
  );
}

function LaunchPanel({
  selected,
  config,
  timeoutMinutes,
  onConfig,
  onTimeout,
  onLaunch,
}: {
  selected: string[];
  config: Config;
  timeoutMinutes: number;
  onConfig: (value: Config) => void;
  onTimeout: (value: number) => void;
  onLaunch: (event: FormEvent<HTMLFormElement>) => void;
}) {
  const set = (key: keyof Config, value: string | number) =>
    onConfig({ ...config, [key]: value });
  return (
    <Card className="border-sky-400/20">
      <CardHeader>
        <CardTitle>Queue Harbor run</CardTitle>
        <CardDescription>
          {selected.length} selected revision{selected.length === 1 ? "" : "s"}{" "}
          · blank instance type uses server-side selection.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={onLaunch} className="space-y-3">
          <FormTable
            rows={[
              [
                "Agent",
                <Input
                  key="agent"
                  value={config.agent}
                  onChange={(event) => set("agent", event.target.value)}
                />,
              ],
              [
                "Model",
                <Input
                  key="model"
                  value={config.model}
                  onChange={(event) => set("model", event.target.value)}
                />,
              ],
              [
                "Attempts",
                <Input
                  key="attempts"
                  type="number"
                  min="1"
                  max="10"
                  value={config.n_attempts}
                  onChange={(event) =>
                    set("n_attempts", Number(event.target.value))
                  }
                />,
              ],
              [
                "Parallelism",
                <Input
                  key="parallelism"
                  type="number"
                  min="1"
                  max="16"
                  value={config.n_concurrent}
                  onChange={(event) =>
                    set("n_concurrent", Number(event.target.value))
                  }
                />,
              ],
              [
                "Instance type",
                <Input
                  key="instance"
                  value={config.instance_type}
                  onChange={(event) => set("instance_type", event.target.value)}
                  placeholder="automatic"
                />,
              ],
              [
                "Root volume (GiB)",
                <Input
                  key="volume"
                  type="number"
                  min="20"
                  value={config.root_volume_gb}
                  onChange={(event) =>
                    set("root_volume_gb", Number(event.target.value))
                  }
                />,
              ],
              [
                "Timeout (minutes)",
                <Input
                  key="timeout"
                  type="number"
                  min="1"
                  max="720"
                  value={timeoutMinutes}
                  onChange={(event) => onTimeout(Number(event.target.value))}
                />,
              ],
            ]}
          />
          <div className="flex justify-end">
            <Button type="submit">
              <Play className="size-3" />
              Queue run
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

function PlansPanel({ plans }: { plans: Plan[] }) {
  return (
    <>
      <Title
        eyebrow="Execution plans"
        title="Approved launch snapshots"
        description="A plan records the exact task revision and runtime configuration before it can become a run."
      />
      <DataCard
        title="Plans"
        description="Immutable configuration snapshots and approval status."
      >
        <Table headers={["Task", "Status", "Agent / instance", "Approved"]}>
          {plans.map((plan) => (
            <tr key={plan.id}>
              <td>
                <code>{plan.task_path}</code>
                <p>{short(plan.id)}</p>
              </td>
              <td>
                <Status state={plan.state} />
              </td>
              <td>
                {String(plan.config.agent || "—")}
                <p>{String(plan.config.instance_type || "automatic")}</p>
              </td>
              <td>
                {plan.approved_by || "—"}
                <p>{when(plan.approved_at)}</p>
              </td>
            </tr>
          ))}
        </Table>
        {!plans.length && (
          <Empty text="No execution plans have been created." />
        )}
      </DataCard>
    </>
  );
}
function RunsPanel({
  runs,
  events,
  onCancel,
  onEvents,
  compact = false,
}: {
  runs: Run[];
  events: Array<{
    id: string;
    event_type: string;
    message: string;
    created_at: string;
  }>;
  onCancel: (id: string) => void;
  onEvents: (id: string) => void;
  compact?: boolean;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Rocket className="size-5 text-sky-300" />
          {compact ? "Recent runs" : "Run queue"}
        </CardTitle>
        <CardDescription>
          Run state is the database truth; instance state follows the worker
          lifecycle.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="overflow-x-auto">
          <Table headers={["Task", "Run state", "EC2 worker", "Deadline", ""]}>
            {runs.map((run) => (
              <tr key={run.id}>
                <td>
                  <code>{run.task_path}</code>
                  <p>{short(run.id)}</p>
                </td>
                <td>
                  <Status state={run.state} />
                </td>
                <td>
                  {run.instance_id ? (
                    <>
                      <code>{run.instance_id}</code>
                      <p>{run.instance_state || "pending"}</p>
                    </>
                  ) : (
                    "Not launched"
                  )}
                </td>
                <td>{when(run.deadline_at)}</td>
                <td className="text-right">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => onEvents(run.id)}
                  >
                    Events
                  </Button>
                  {["queued", "provisioning", "running"].includes(
                    run.state,
                  ) && (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="ml-1 text-rose-200 hover:text-rose-100"
                      onClick={() => onCancel(run.id)}
                    >
                      <StopCircle className="size-4" />
                      Cancel
                    </Button>
                  )}
                </td>
              </tr>
            ))}
          </Table>
          {!runs.length && (
            <Empty text="No benchmark runs are queued or recorded." />
          )}
        </div>
        {events.length > 0 && (
          <div className="mt-4 border-t border-slate-800 pt-4">
            <p className="mb-2 font-mono text-xs uppercase tracking-wide text-slate-400">
              Selected run events
            </p>
            {events.map((event) => (
              <div
                key={event.id}
                className="border-l border-sky-400/40 py-1 pl-3 text-sm"
              >
                <span className="text-sky-200">{event.event_type}</span>{" "}
                <span>{event.message}</span>
                <span className="ml-2 text-xs text-slate-500">
                  {when(event.created_at)}
                </span>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
function JobsPanel({ jobs }: { jobs: Job[] }) {
  return (
    <>
      <Title
        eyebrow="Worker jobs"
        title="Database-backed dispatch queue"
        description="No Redis or Celery: leases, retries, and launch work are stored in SQLite."
      />
      <DataCard
        title="Jobs"
        description="The runner leases an available job then launches or terminates the worker."
      >
        <Table headers={["Kind", "Status", "Attempts", "Lease", "Error"]}>
          {jobs.map((job) => (
            <tr key={job.id}>
              <td>
                <code>{job.kind}</code>
                <p>{short(job.id)}</p>
              </td>
              <td>
                <Status state={job.state} />
              </td>
              <td>
                {job.attempts} / {job.max_attempts}
              </td>
              <td>
                {job.lease_owner || "—"}
                <p>{when(job.lease_expires_at)}</p>
              </td>
              <td className="max-w-xs truncate text-rose-200">
                {job.last_error || "—"}
              </td>
            </tr>
          ))}
        </Table>
        {!jobs.length && <Empty text="No pending or recent database jobs." />}
      </DataCard>
    </>
  );
}
function WebhooksPanel({
  deliveries,
  loading,
  onResend,
}: {
  deliveries: WebhookDelivery[];
  loading: boolean;
  onResend: (delivery: WebhookDelivery) => void;
}) {
  return (
    <>
      <Title
        eyebrow="Outbound notifications"
        title="Discord webhook deliveries"
        description="Inspect the exact destination, payload, response, and retry state for proposal and review notifications."
      />
      <DataCard
        title="Delivery history"
        description="Failed and completed deliveries can be queued again manually; completed sends require confirmation."
      >
        <Table
          headers={[
            "Event",
            "Status",
            "Attempts",
            "Destination",
            "Content",
            "Result",
            "",
          ]}
        >
          {deliveries.map((delivery) => (
            <tr key={delivery.id}>
              <td className="min-w-44 align-top">
                <code className="text-xs text-sky-200">
                  {delivery.event_type}
                </code>
                <p className="mt-1 text-xs text-slate-500">
                  {when(delivery.created_at)} · {short(delivery.id)}
                </p>
              </td>
              <td className="align-top">
                <Status state={delivery.state} />
              </td>
              <td className="align-top">
                {delivery.attempts} / {delivery.max_attempts}
                <p className="mt-1 text-xs text-slate-500">
                  Next: {when(delivery.available_at)}
                </p>
              </td>
              <td className="max-w-64 align-top">
                <code className="block break-all text-[11px] leading-5 text-slate-300">
                  {delivery.destination_url}
                </code>
              </td>
              <td className="min-w-44 align-top">
                <details>
                  <summary className="cursor-pointer text-xs text-sky-300">
                    View JSON payload
                  </summary>
                  <pre className="mt-2 max-h-64 max-w-md overflow-auto whitespace-pre-wrap break-all rounded bg-slate-950 p-2 text-[10px] leading-4 text-slate-300">
                    {JSON.stringify(delivery.payload, null, 2)}
                  </pre>
                </details>
              </td>
              <td className="min-w-44 align-top text-xs text-slate-400">
                <p>
                  {delivery.response_status
                    ? `HTTP ${delivery.response_status}`
                    : "No response yet"}
                </p>
                <p>{delivery.sent_at ? `Sent ${when(delivery.sent_at)}` : ""}</p>
                {(delivery.last_error || delivery.response_body) && (
                  <details className="mt-1">
                    <summary className="cursor-pointer text-rose-200">
                      Response details
                    </summary>
                    <pre className="mt-2 max-h-40 max-w-sm overflow-auto whitespace-pre-wrap break-all rounded bg-slate-950 p-2 text-[10px] leading-4">
                      {delivery.last_error || delivery.response_body}
                    </pre>
                  </details>
                )}
              </td>
              <td className="align-top text-right">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={loading || delivery.state === "sending"}
                  onClick={() => onResend(delivery)}
                >
                  <RefreshCw className="size-3.5" />
                  Resend
                </Button>
              </td>
            </tr>
          ))}
        </Table>
        {!deliveries.length && (
          <Empty text="No proposal or review webhook deliveries have been recorded." />
        )}
      </DataCard>
    </>
  );
}
function CloudPanel({
  profiles,
  profileName,
  allocation,
  onName,
  onAllocation,
  onSubmit,
}: {
  profiles: CloudProfile[];
  profileName: string;
  allocation: string;
  onName: (value: string) => void;
  onAllocation: (value: string) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
}) {
  return (
    <>
      <Title
        eyebrow="Cloud resources"
        title="EC2 execution allocations"
        description="Profiles capture operational limits. AWS credentials, OAuth secrets, and worker tokens remain outside the UI."
      />
      <div className="grid gap-5 xl:grid-cols-[1fr_.9fr]">
        <DataCard
          title="Saved allocation profiles"
          description="Non-secret constraints used by operators."
        >
          <div className="space-y-3">
            {profiles.map((profile) => (
              <div
                key={profile.id}
                className="rounded-lg border border-slate-800 bg-slate-900/50 p-4"
              >
                <div className="flex items-center justify-between">
                  <p className="font-medium">{profile.name}</p>
                  <Status state={profile.enabled ? "enabled" : "disabled"} />
                </div>
                <pre className="mt-3 overflow-x-auto text-xs text-slate-400">
                  {JSON.stringify(profile.allocation, null, 2)}
                </pre>
              </div>
            ))}
            {!profiles.length && (
              <Empty text="No cloud allocation profiles saved." />
            )}
          </div>
        </DataCard>
        <Card>
          <CardHeader>
            <CardTitle>Save allocation profile</CardTitle>
            <CardDescription>
              Use this for a named operating envelope, not secrets.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <form onSubmit={onSubmit} className="space-y-4">
              <Field label="Profile name">
                <Input
                  value={profileName}
                  onChange={(event) => onName(event.target.value)}
                  placeholder="sandbox-us-east-1"
                />
              </Field>
              <Field label="Allocation JSON">
                <Textarea
                  value={allocation}
                  onChange={(event) => onAllocation(event.target.value)}
                />
              </Field>
              <Button type="submit">
                <CloudCog className="size-4" />
                Save profile
              </Button>
            </form>
          </CardContent>
        </Card>
      </div>
    </>
  );
}

function DatabaseSnapshotsPanel({
  snapshots,
  loading,
  onSave,
}: {
  snapshots: DatabaseSnapshot[];
  loading: boolean;
  onSave: () => void;
}) {
  return (
    <>
      <Title
        eyebrow="Database cache"
        title="SQLite snapshots"
        description="Save an online-consistent copy of the control-plane database to the server cache, then download a named recovery point."
      />
      <div className="grid gap-5 xl:grid-cols-[0.85fr_1.15fr]">
        <DataCard
          title="Save current database"
          description="The server creates the snapshot directly from SQLite; active requests can continue."
        >
          <div className="space-y-4">
            <p className="text-sm leading-6 text-slate-300">
              Use this before a migration, bulk import, or operational change.
              Saved files remain in the server cache until they are removed on the host.
            </p>
            <Button type="button" onClick={onSave} disabled={loading}>
              <Database className={`size-4 ${loading ? "animate-pulse" : ""}`} />
              {loading ? "Saving snapshot..." : "Save SQLite snapshot"}
            </Button>
          </div>
        </DataCard>
        <DataCard
          title="Available downloads"
          description="Each item is an immutable SQLite file from the cache."
        >
          <Table headers={["Snapshot", "Created", "Size", ""]}>
            {snapshots.map((snapshot) => (
              <tr key={snapshot.name}>
                <td>
                  <code className="text-xs text-slate-200">{snapshot.name}</code>
                </td>
                <td>{when(snapshot.created_at)}</td>
                <td>{bytes(snapshot.size_bytes)}</td>
                <td className="text-right">
                  <a
                    className="inline-flex items-center gap-1 text-sky-300 hover:text-sky-200"
                    href={databaseSnapshotDownloadUrl(snapshot.name)}
                    download
                  >
                    <Download className="size-3.5" />
                    Download
                  </a>
                </td>
              </tr>
            ))}
            {!snapshots.length && (
              <Empty text="No SQLite snapshots saved yet. Create one to make it available for download." />
            )}
          </Table>
        </DataCard>
      </div>
    </>
  );
}

function ProposalPanel({
  proposal,
  proposalDomains,
  onChange,
  result,
  recent = [],
  ready,
  onSubmit,
}: {
  proposal: ProposalInput;
  proposalDomains: string[];
  onChange: (value: ProposalInput) => void;
  result: Proposal | null;
  recent?: Proposal[];
  ready: boolean;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
}) {
  const selectedDomains = new Set(
    proposal.domain
      .split(",")
      .map((domain) => domain.trim())
      .filter(Boolean),
  );
  const toggleDomain = (domain: string, checked: boolean) => {
    const next = new Set(selectedDomains);
    if (checked) next.add(domain);
    else next.delete(domain);
    onChange({ ...proposal, domain: [...next].join(", ") });
  };

  return (
    <>
      <Title
        eyebrow="Contribution intake"
        title="Task proposals"
        description="This form uses the same scientist-facing fields as the public Website and opens a GitHub Discussion for review."
      />
      <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1fr)_19rem]">
        <Card>
          <CardHeader className="border-b border-slate-800">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <CardTitle>New proposal</CardTitle>
                <CardDescription>
                  Use the same inputs contributors see on the public Website.
                </CardDescription>
              </div>
              <div className="flex items-center gap-2">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => onChange(sampleProposal)}
                >
                  Fill example
                </Button>
                <Badge
                  className={
                    ready
                      ? "border-emerald-400/30 bg-emerald-400/10 text-emerald-200"
                      : "border-slate-700 bg-slate-900 text-slate-400"
                  }
                >
                  {ready ? "Ready" : "Incomplete"}
                </Badge>
              </div>
            </div>
          </CardHeader>
          <CardContent className="pt-4">
            <form onSubmit={onSubmit} className="space-y-3">
              <FormTable
                rows={[
                  [
                    "Title",
                    <Input
                      key="title"
                      value={proposal.title}
                      onChange={(event) =>
                        onChange({ ...proposal, title: event.target.value })
                      }
                      placeholder="Reconstruct a sparse coastal sensor field"
                    />,
                  ],
                  [
                    "Domains involved",
                    <div
                      key="classification"
                      className="space-y-3"
                    >
                      <div
                        role="group"
                        aria-label="Domains involved"
                        className="grid gap-2 sm:grid-cols-2"
                      >
                        {proposalDomains.map((domain) => (
                          <label
                            key={domain}
                            className="flex cursor-pointer items-center gap-2 rounded-md border border-slate-800 bg-slate-950/40 px-3 py-2 text-sm text-slate-200 transition-colors hover:border-sky-400/50"
                          >
                            <input
                              type="checkbox"
                              checked={selectedDomains.has(domain)}
                              onChange={(event) => toggleDomain(domain, event.target.checked)}
                              className="size-4 accent-sky-400"
                            />
                            {domain}
                          </label>
                        ))}
                      </div>
                      <p className="text-xs text-slate-500">
                        Selected domains are submitted as one comma-separated string.
                      </p>
                      <Input
                        aria-label="Specific field"
                        value={proposal.field_name}
                        onChange={(event) =>
                          onChange({
                            ...proposal,
                            field_name: event.target.value,
                          })
                        }
                        placeholder="e.g. Coastal oceanography"
                      />
                    </div>,
                  ],
                  [
                    "Problem specification",
                    <Textarea
                      key="problem"
                      className="min-h-28"
                      value={proposal.problem}
                      onChange={(event) =>
                        onChange({
                          ...proposal,
                          problem: event.target.value,
                        })
                      }
                      placeholder="What scientific problem does this task address, and why is it important?"
                    />,
                  ],
                  [
                    "Solvability",
                    <Textarea
                      key="solvability"
                      className="min-h-20"
                      value={proposal.solvability}
                      onChange={(event) =>
                        onChange({
                          ...proposal,
                          solvability: event.target.value,
                        })
                      }
                      placeholder="Is this problem solvable in principle? Does the difficulty matter in a good way?"
                    />,
                  ],
                  [
                    "References & resources",
                    <Textarea
                      key="references"
                      className="min-h-20"
                      value={proposal.references}
                      onChange={(event) =>
                        onChange({
                          ...proposal,
                          references: event.target.value,
                        })
                      }
                      placeholder="Papers, datasets, code, or protocols this task builds on."
                    />,
                  ],
                  [
                    "Software and tools",
                    <Textarea
                      key="software"
                      className="min-h-20"
                      value={proposal.software}
                      onChange={(event) =>
                        onChange({ ...proposal, software: event.target.value })
                      }
                      placeholder="The tools, software, and dependencies required for this task."
                    />,
                  ],
                  [
                    "Dataset & artifacts",
                    <Textarea
                      key="dataset"
                      className="min-h-20"
                      value={proposal.dataset}
                      onChange={(event) =>
                        onChange({
                          ...proposal,
                          dataset: event.target.value,
                        })
                      }
                      placeholder="Data provenance, license, visibility, and hidden verifier artifacts."
                    />,
                  ],
                  [
                    "Computation resources",
                    <Textarea
                      key="compute"
                      className="min-h-20"
                      value={proposal.compute}
                      onChange={(event) =>
                        onChange({
                          ...proposal,
                          compute: event.target.value,
                        })
                      }
                      placeholder="Estimated time plus CPU, GPU, memory, storage, and device requirements."
                    />,
                  ],
                  [
                    "Expected workflow & outputs",
                    <Textarea
                      key="workflow"
                      className="min-h-24"
                      value={proposal.workflow}
                      onChange={(event) =>
                        onChange({
                          ...proposal,
                          workflow: event.target.value,
                        })
                      }
                      placeholder="What the agent starts with, what it does, and what it hands back."
                    />,
                  ],
                  [
                    "How will this task be evaluated?",
                    <Textarea
                      key="evaluation"
                      className="min-h-24"
                      value={proposal.evaluation}
                      onChange={(event) =>
                        onChange({
                          ...proposal,
                          evaluation: event.target.value,
                        })
                      }
                      placeholder="What metric or verification procedure determines success?"
                    />,
                  ],
                  [
                    "Cheating & leakage risk",
                    <Textarea
                      key="leakage"
                      className="min-h-20"
                      value={proposal.leakage}
                      onChange={(event) =>
                        onChange({ ...proposal, leakage: event.target.value })
                      }
                      placeholder="Could open resources weaken the task or evaluation?"
                    />,
                  ],
                  [
                    "Name",
                    <Input
                      key="name"
                      value={proposal.name}
                      onChange={(event) =>
                        onChange({ ...proposal, name: event.target.value })
                      }
                    />,
                  ],
                  [
                    "Institution / affiliation",
                    <Input
                      key="affiliation"
                      value={proposal.affiliation}
                      onChange={(event) =>
                        onChange({ ...proposal, affiliation: event.target.value })
                      }
                    />,
                  ],
                  [
                    "GitHub username",
                    <Input
                      key="github"
                      value={proposal.github}
                      onChange={(event) =>
                        onChange({ ...proposal, github: event.target.value })
                      }
                      placeholder="octocat"
                    />,
                  ],
                ]}
              />
              {result && (
                <a
                  className="mt-3 flex items-center gap-2 border border-emerald-400/30 bg-emerald-400/10 px-3 py-2 text-sm text-emerald-100"
                  href={result.discussion_url}
                  target="_blank"
                  rel="noreferrer"
                >
                  <CheckCircle2 className="size-4" />
                  Discussion opened <code>{short(result.id)}</code>
                  <ExternalLink className="ml-auto size-4" />
                </a>
              )}
              <div className="mt-3 flex items-center justify-between gap-3 border-t border-slate-800 pt-3">
                <p className="text-xs text-slate-500">
                  Uses the same required fields and limits as the Website submission wizard.
                </p>
                <Button type="submit" disabled={!ready}>
                  <Send className="size-4" />
                  Open Discussion
                </Button>
              </div>
            </form>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Review path</CardTitle>
            <CardDescription>Required gates after submission.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2 text-xs text-slate-300">
            <Step number="01" text="Proposal Discussion" />
            <Step number="02" text="Domain and technical review" />
            <Step number="03" text="Harbor task Pull Request" />
            <Step number="04" text="CI, Oracle and Nop validation" />
          </CardContent>
        </Card>
      </div>
      {recent.length > 0 && (
        <div className="mt-4">
          <DataCard
            title="Recent proposals"
            description="Latest proposal records persisted by the control plane."
          >
            <Table
              headers={[
                "Proposal",
                "Classification",
                "Input",
                "Status",
                "Contributor",
                "Discussion",
              ]}
            >
              {recent.slice(0, 12).map((item) => (
                <tr key={item.id}>
                  <td>
                    <span className="font-medium text-slate-100">
                      {item.title}
                    </span>
                    <p>
                      <code>{item.task_slug}</code>
                    </p>
                  </td>
                  <td>
                    <code>{item.domain}</code>
                    <p>{item.field}</p>
                  </td>
                  <td>
                    <Status state={item.input_valid ? "valid" : "invalid"} />
                  </td>
                  <td>
                    <Status state={item.status} />
                  </td>
                  <td>{item.author_login ? `@${item.author_login}` : "—"}</td>
                  <td>
                    {item.discussion_url ? (
                      <a
                        className="inline-flex items-center gap-1 text-sky-300 hover:text-sky-200"
                        href={item.discussion_url}
                        target="_blank"
                        rel="noreferrer"
                      >
                        Open <ExternalLink className="size-3" />
                      </a>
                    ) : (
                      "Pending"
                    )}
                  </td>
                </tr>
              ))}
            </Table>
          </DataCard>
        </div>
      )}
    </>
  );
}

function Sidebar({
  active,
  admin,
  onChange,
}: {
  active: View;
  admin: boolean;
  onChange: (view: View) => void;
}) {
  const items: Array<[View, string, typeof Activity]> = admin
    ? [
        ["proposals", "Task proposals", Send],
        ["overview", "Operations", Activity],
        ["tasks", "Task library", Database],
        ["plans", "Execution plans", SquareStack],
        ["runs", "Run queue", Rocket],
        ["jobs", "Worker jobs", ServerCog],
        ["webhooks", "Discord webhooks", Webhook],
        ["cloud", "Cloud resources", CloudCog],
        ["snapshots", "Database snapshots", History],
      ]
    : [["proposals", "Task proposals", Send]];
  return (
    <aside>
      <div className="sticky top-20 border border-slate-800 bg-slate-950/60 p-2">
        <p className="mb-2 px-2 py-1 font-mono text-[10px] uppercase tracking-[0.14em] text-sky-300">
          {admin ? "Control plane" : "Contribute"}
        </p>
        {items.map(([id, label, Icon]) => (
          <button
            key={id}
            onClick={() => onChange(id)}
            className={`mb-0.5 flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-xs ${active === id ? "bg-slate-800 text-white" : "text-slate-400 hover:bg-slate-900 hover:text-white"}`}
          >
            <Icon className="size-3.5" />
            {label}
          </button>
        ))}
        <a
          href="https://github.com/hycarbon-b/ai4sbench-benchmark"
          target="_blank"
          rel="noreferrer"
          className="mt-2 flex items-center gap-2 border-t border-slate-800 px-2.5 pt-3 text-xs text-slate-500 hover:text-slate-200"
        >
          <Github className="size-3.5" />
          Repository <ExternalLink className="ml-auto size-3" />
        </a>
      </div>
    </aside>
  );
}
function UserMenu({ user, onLogout }: { user: User; onLogout: () => void }) {
  const identity = user.github_login || user.email;
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <button className="flex items-center gap-2 rounded-md p-1 pr-2 text-sm hover:bg-slate-800">
          <Avatar.Root className="grid size-7 place-items-center rounded-full bg-sky-400 text-xs font-bold text-slate-950">
            <Avatar.Fallback>
              {identity.slice(0, 2).toUpperCase()}
            </Avatar.Fallback>
          </Avatar.Root>
          <span className="hidden sm:inline">@{identity}</span>
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          align="end"
          className="z-50 min-w-52 rounded-lg border border-slate-700 bg-slate-950 p-1 shadow-xl"
        >
          <div className="border-b border-slate-800 px-3 py-2 text-xs text-slate-400">
            <p className="text-sm text-slate-100">@{identity}</p>
            <p>{user.role}</p>
          </div>
          <DropdownMenu.Item
            onSelect={onLogout}
            className="mt-1 flex cursor-pointer items-center gap-2 rounded-md px-3 py-2 text-sm outline-none hover:bg-slate-800"
          >
            <LogOut className="size-4" />
            Sign out
          </DropdownMenu.Item>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
function Notice({
  error,
  message,
  onClose,
}: {
  error: string;
  message: string;
  onClose: () => void;
}) {
  return (
    <div
      className={`flex justify-between gap-3 rounded-lg border px-4 py-3 text-sm ${error ? "border-rose-400/30 bg-rose-400/10 text-rose-100" : "border-emerald-400/30 bg-emerald-400/10 text-emerald-100"}`}
    >
      <span>{error || message}</span>
      <button onClick={onClose}>×</button>
    </div>
  );
}
function Title({
  eyebrow,
  title,
  description,
}: {
  eyebrow: string;
  title: string;
  description: string;
}) {
  return (
    <div className="border-b border-slate-800 pb-3">
      <p className="font-mono text-[11px] uppercase tracking-[0.14em] text-sky-300">
        {eyebrow}
      </p>
      <h1 className="mt-1 text-xl font-semibold tracking-tight text-white">
        {title}
      </h1>
      <p className="mt-1 max-w-3xl text-xs leading-5 text-slate-500">
        {description}
      </p>
    </div>
  );
}
function Metric({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: "amber" | "sky" | "emerald" | "rose";
}) {
  const colors = {
    amber: "text-amber-200",
    sky: "text-sky-200",
    emerald: "text-emerald-200",
    rose: "text-rose-200",
  };
  return (
    <Card>
      <CardContent className="p-3">
        <p className="font-mono text-[11px] uppercase tracking-wide text-slate-500">
          {label}
        </p>
        <p className={`mt-1 text-2xl font-semibold ${colors[tone]}`}>{value}</p>
      </CardContent>
    </Card>
  );
}
function Status({ state }: { state: string }) {
  const tone = ["succeeded", "approved", "running", "enabled", "valid", "completed"].includes(state)
    ? "border-emerald-400/30 bg-emerald-400/10 text-emerald-200"
    : ["failed", "launch_failed", "termination_failed", "invalid"].includes(state)
      ? "border-rose-400/30 bg-rose-400/10 text-rose-200"
      : "border-amber-400/30 bg-amber-400/10 text-amber-100";
  return <Badge className={tone}>{state.replaceAll("_", " ")}</Badge>;
}
function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid gap-2">
      <Label>{label}</Label>
      {children}
    </div>
  );
}
function FormTable({ rows }: { rows: Array<[string, ReactNode]> }) {
  return (
    <UiTable>
      <TableBody>
        {rows.map(([label, control]) => (
          <TableRow key={label}>
            <TableHead className="w-36 bg-slate-900/30 text-left normal-case tracking-normal text-slate-400">
              {label}
            </TableHead>
            <TableCell>{control}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </UiTable>
  );
}
function DataCard({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children: ReactNode;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent className="overflow-x-auto">{children}</CardContent>
    </Card>
  );
}
function Table({
  headers,
  children,
}: {
  headers: string[];
  children: ReactNode;
}) {
  return (
    <UiTable className="min-w-[650px]">
      <TableHeader>
        <TableRow className="hover:bg-slate-900/70">
          {headers.map((header, index) => (
            <TableHead key={`${header}-${index}`}>{header}</TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>{children}</TableBody>
    </UiTable>
  );
}
function Empty({ text }: { text: string }) {
  return <div className="p-6 text-center text-sm text-slate-500">{text}</div>;
}
function Step({ number, text }: { number: string; text: string }) {
  return (
    <div className="flex gap-3">
      <span className="font-mono text-sky-300">{number}</span>
      <span>{text}</span>
    </div>
  );
}
