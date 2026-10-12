import { CheckCircle2, ChevronRight, RefreshCw, Send } from "lucide-react";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";

import { listMailRecipients, sendBulkMail, type BulkMailInput, type MailRecipient } from "./api";
import { Badge } from "./components/ui/badge";
import { Button } from "./components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "./components/ui/dialog";
import { Input } from "./components/ui/input";
import { Label } from "./components/ui/label";
import { Textarea } from "./components/ui/textarea";

type Audience = "proposals" | "reviewers";

export function MailComposer({
  onQueued,
  onOpenDeliveries,
}: {
  onQueued: () => void;
  onOpenDeliveries: () => void;
}) {
  const [recipients, setRecipients] = useState<MailRecipient[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [draftSelected, setDraftSelected] = useState<string[]>([]);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [audience, setAudience] = useState<Audience>("proposals");
  const [search, setSearch] = useState("");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [signature, setSignature] = useState("AI4S-Bench Team");
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState("");
  const lastRequest = useRef<{ content: string; key: string } | null>(null);

  const refresh = async () => {
    setLoading(true);
    setError("");
    try {
      const response = await listMailRecipients();
      setRecipients(response.items);
      setSelected((current) => current.filter((id) => response.items.some((item) => item.user_id === id)));
      setDraftSelected((current) => current.filter((id) => response.items.some((item) => item.user_id === id)));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not load OAuth email recipients.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void refresh(); }, []);

  const group = useMemo(
    () => recipients.filter((item) => audience === "proposals" ? item.proposal_count > 0 : item.reviewer_statuses.length > 0),
    [audience, recipients],
  );
  const visible = useMemo(() => {
    const query = search.trim().toLowerCase();
    return group.filter((item) =>
      !query || [item.email, item.github_login || ""].some((value) => value.toLowerCase().includes(query)),
    );
  }, [group, search]);
  const selectedRecipients = recipients.filter((item) => selected.includes(item.user_id));
  const addressCount = new Set(selectedRecipients.map((item) => item.email.toLowerCase())).size;
  const draftRecipients = recipients.filter((item) => draftSelected.includes(item.user_id));
  const draftAddressCount = new Set(draftRecipients.map((item) => item.email.toLowerCase())).size;
  const allVisibleSelected = visible.length > 0 && visible.every((item) => draftSelected.includes(item.user_id));

  const openPicker = () => {
    setDraftSelected(selected);
    setSearch("");
    setPickerOpen(true);
  };
  const toggle = (id: string) => setDraftSelected((current) =>
    current.includes(id) ? current.filter((item) => item !== id) : [...current, id],
  );
  const toggleVisible = () => setDraftSelected((current) => allVisibleSelected
    ? current.filter((id) => !visible.some((item) => item.user_id === id))
    : [...new Set([...current, ...visible.map((item) => item.user_id)])],
  );
  const applyRecipients = () => {
    setSelected(draftSelected);
    setPickerOpen(false);
    setResult("");
  };

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!addressCount || addressCount > 100) return;
    if (!window.confirm(`Queue ${addressCount} individual email${addressCount === 1 ? "" : "s"} with the subject “${subject.trim()}”?`)) return;
    const input: BulkMailInput = {
      recipient_ids: selected,
      subject: subject.trim(),
      body: body.trim(),
      signature: signature.trim(),
    };
    const content = JSON.stringify(input);
    const key = lastRequest.current?.content === content ? lastRequest.current.key : crypto.randomUUID();
    lastRequest.current = { content, key };
    setSending(true);
    setError("");
    setResult("");
    try {
      const response = await sendBulkMail(input, key);
      setResult(`${response.queued_count} individual email${response.queued_count === 1 ? "" : "s"} queued.`);
      setSelected([]);
      lastRequest.current = null;
      onQueued();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not queue the emails.");
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="border-b border-slate-800 pb-3">
        <div>
          <p className="font-mono text-[11px] uppercase tracking-[0.14em] text-sky-300">Outbound operations</p>
          <h1 className="mt-1 text-xl font-semibold tracking-tight text-white">Send email</h1>
          <p className="mt-1 max-w-3xl text-xs leading-5 text-slate-500">Select saved GitHub OAuth addresses and queue one private delivery per address.</p>
        </div>
      </div>
      {error && <p role="alert" className="border border-red-900 bg-red-950/40 p-3 text-sm text-red-300">{error}</p>}
      {result && (
        <div role="status" className="flex max-w-4xl gap-3 border border-emerald-900 bg-emerald-950/40 p-4 text-sm text-emerald-200">
          <CheckCircle2 className="mt-0.5 size-4 shrink-0" />
          <div className="space-y-1">
            <p className="font-medium">{result}</p>
            <p className="text-xs text-emerald-100/80">Delivery continues in the background. Queued does not mean the SMTP server has accepted the message yet.</p>
            <p className="text-xs text-emerald-100/80">
              Find the results in System → <button type="button" className="font-medium underline underline-offset-2 hover:text-white" onClick={onOpenDeliveries}>Outbound deliveries</button> → type <code>smtp</code>, event <code>admin_bulk_email</code>.
            </p>
          </div>
        </div>
      )}
      <form className="max-w-4xl" onSubmit={(event) => void submit(event)}>
          <Card>
            <CardHeader>
              <CardTitle>Message</CardTitle>
              <CardDescription>Uses the existing AI4S-Bench email layout. The SMTP sender address remains configured on the server.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-1.5">
                <Label htmlFor="mail-recipients">Recipients</Label>
                <button id="mail-recipients" type="button" className="flex w-full items-center justify-between gap-3 rounded-sm border border-slate-700 bg-slate-950 px-3 py-2 text-left text-sm text-slate-200 hover:border-sky-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-400" onClick={openPicker} disabled={sending}>
                  <span>{addressCount ? `${addressCount} unique OAuth address${addressCount === 1 ? "" : "es"} selected` : "Choose proposal authors or reviewer applicants"}</span>
                  <ChevronRight className="size-4 shrink-0 text-slate-400" />
                </button>
                <p className="text-xs text-slate-500">Each address receives a separate email; duplicate addresses are sent only once.</p>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="mail-subject">Subject</Label>
                <Input id="mail-subject" maxLength={200} required value={subject} onChange={(event) => setSubject(event.target.value)} placeholder="A short, specific subject" />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="mail-body">Body</Label>
                <Textarea id="mail-body" className="min-h-48" maxLength={20000} required value={body} onChange={(event) => setBody(event.target.value)} placeholder="Write the message here…" />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="mail-signature">Footer sender / signature</Label>
                <Input id="mail-signature" maxLength={120} required value={signature} onChange={(event) => setSignature(event.target.value)} />
              </div>
              <details className="border border-slate-800 bg-slate-950/50 p-4">
                <summary className="cursor-pointer text-xs text-slate-400">Email preview</summary>
                <p className="mt-3 font-semibold text-slate-100">{subject || "Subject"}</p>
                <p className="mt-3 whitespace-pre-wrap break-words text-sm leading-6 text-slate-300">{body || "Your message will appear here."}</p>
                <p className="mt-5 text-sm text-slate-300">{signature || "Signature"}</p>
              </details>
              <div className="flex items-center justify-between gap-3">
                <p className="text-xs text-slate-500">Maximum 100 people per send.</p>
                <Button type="submit" disabled={sending || !addressCount || selected.length > 100 || !subject.trim() || !body.trim() || !signature.trim()}>
                  <Send className="size-3.5" /> {sending ? "Queueing…" : `Send to ${addressCount}`}
                </Button>
              </div>
            </CardContent>
          </Card>
      </form>
      <Dialog open={pickerOpen} onOpenChange={setPickerOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle className="text-lg font-semibold text-white">Choose recipients</DialogTitle>
            <DialogDescription className="text-sm text-slate-400">
              Select saved GitHub OAuth addresses. Form-entered reviewer email addresses are not used.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            {error && <p role="alert" className="border border-red-900 bg-red-950/40 p-3 text-sm text-red-300">{error}</p>}
            <div className="flex gap-2">
              <Button type="button" size="sm" variant={audience === "proposals" ? "default" : "outline"} onClick={() => setAudience("proposals")}>
                Proposal authors ({recipients.filter((item) => item.proposal_count > 0).length})
              </Button>
              <Button type="button" size="sm" variant={audience === "reviewers" ? "default" : "outline"} onClick={() => setAudience("reviewers")}>
                Reviewer applications ({recipients.filter((item) => item.reviewer_statuses.length > 0).length})
              </Button>
            </div>
            <div className="flex gap-2">
              <Input aria-label="Search OAuth recipients" placeholder="Search GitHub login or email" value={search} onChange={(event) => setSearch(event.target.value)} />
              <Button type="button" variant="outline" size="sm" onClick={() => void refresh()} disabled={loading} aria-label="Refresh recipients">
                <RefreshCw className={`size-3.5 ${loading ? "animate-spin" : ""}`} />
              </Button>
            </div>
            <div className="flex items-center justify-between text-xs text-slate-400">
              <span>{visible.length} shown</span>
              <Button type="button" variant="ghost" size="sm" onClick={toggleVisible} disabled={!visible.length || loading}>
                {allVisibleSelected ? "Clear shown" : "Select shown"}
              </Button>
            </div>
            <div className="max-h-[22rem] overflow-y-auto border border-slate-800">
              {visible.map((item) => (
                <label key={item.user_id} className="flex cursor-pointer items-start gap-3 border-b border-slate-800 px-3 py-2.5 last:border-0 hover:bg-slate-900/60">
                  <input type="checkbox" className="mt-1 accent-sky-400" checked={draftSelected.includes(item.user_id)} onChange={() => toggle(item.user_id)} disabled={loading} />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm text-slate-100">{item.github_login ? `@${item.github_login}` : item.email}</span>
                    <span className="block truncate text-xs text-slate-400">{item.email}</span>
                    <span className="mt-1 flex flex-wrap gap-1">
                      {item.proposal_count > 0 && <Badge>{item.proposal_count} proposal{item.proposal_count === 1 ? "" : "s"}</Badge>}
                      {item.reviewer_statuses.map((status) => <Badge key={status}>Reviewer · {status}</Badge>)}
                    </span>
                  </span>
                </label>
              ))}
              {!loading && !visible.length && <p className="p-4 text-sm text-slate-500">No matching OAuth email addresses.</p>}
              {loading && <p className="p-4 text-sm text-slate-500">Loading recipients…</p>}
            </div>
            <div className="flex items-center justify-between gap-3 border-t border-slate-800 pt-3">
              <p className={`text-xs ${draftSelected.length > 100 ? "text-rose-300" : "text-slate-400"}`}>
                {draftSelected.length} people · {draftAddressCount} unique address{draftAddressCount === 1 ? "" : "es"}
                {draftSelected.length > 100 && " · Select at most 100 people"}
              </p>
              <div className="flex gap-2">
                <Button type="button" variant="outline" size="sm" onClick={() => setPickerOpen(false)}>Cancel</Button>
                <Button type="button" size="sm" onClick={applyRecipients} disabled={loading || draftSelected.length > 100}>Use recipients</Button>
              </div>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
