import { Send } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { api, sendBulkMail, type BulkMailInput } from "./api";
import { Button } from "./components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "./components/ui/dialog";
import { Input } from "./components/ui/input";
import { Label } from "./components/ui/label";
import { Textarea } from "./components/ui/textarea";

export function MailComposer({ recipientIds, onClose, onQueued }: {
  recipientIds: string[];
  onClose: () => void;
  onQueued: (count: number) => void;
}) {
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [signature, setSignature] = useState("AI4S-Bench Team");
  const [preview, setPreview] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const lastRequest = useRef<{ content: string; key: string } | null>(null);
  const ready = recipientIds.length > 0 && recipientIds.length <= 100 && !!subject.trim() && !!body.trim() && !!signature.trim();

  useEffect(() => { setPreview(""); setConfirming(false); }, [subject, body, signature]);

  const showPreview = async () => {
    if (!ready) return;
    setBusy(true);
    setError("");
    try {
      const rendered = await api<{ html: string }>("/api/v1/admin-mail/preview", {
        method: "POST", body: JSON.stringify({ subject, body, signature }),
      });
      setPreview(rendered.html);
      setConfirming(true);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not render email preview.");
    } finally { setBusy(false); }
  };

  const send = async () => {
    if (!ready || !confirming) return;
    const input: BulkMailInput = { recipient_ids: recipientIds, subject: subject.trim(), body: body.trim(), signature: signature.trim() };
    const content = JSON.stringify(input);
    const key = lastRequest.current?.content === content ? lastRequest.current.key : crypto.randomUUID();
    lastRequest.current = { content, key };
    setBusy(true);
    setError("");
    try {
      const result = await sendBulkMail(input, key);
      lastRequest.current = null;
      onQueued(result.queued_count);
      onClose();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not queue emails.");
    } finally { setBusy(false); }
  };

  return <Dialog open onOpenChange={(open) => { if (!open && !busy) onClose(); }}>
    <DialogContent className="max-h-[90vh] max-w-3xl overflow-y-auto">
      <DialogHeader>
        <DialogTitle>Send email</DialogTitle>
        <DialogDescription>{recipientIds.length} selected user{recipientIds.length === 1 ? "" : "s"}. Each address receives a private message; duplicate addresses are sent once.</DialogDescription>
      </DialogHeader>
      {error && <p role="alert" className="text-sm text-rose-300">{error}</p>}
      <div className="space-y-4">
        <div className="space-y-1"><Label htmlFor="mail-subject">Subject</Label><Input id="mail-subject" maxLength={200} value={subject} onChange={(event) => setSubject(event.target.value)} /></div>
        <div className="space-y-1"><Label htmlFor="mail-body">Body</Label><Textarea id="mail-body" className="min-h-36" maxLength={20000} value={body} onChange={(event) => setBody(event.target.value)} /></div>
        <div className="space-y-1"><Label htmlFor="mail-signature">Signature</Label><Input id="mail-signature" maxLength={120} value={signature} onChange={(event) => setSignature(event.target.value)} /></div>
        {confirming && <div className="space-y-2"><p className="text-sm font-medium text-slate-200">Full HTML preview</p><iframe title="Email preview" sandbox="" srcDoc={preview} className="h-80 w-full rounded-sm border border-slate-700 bg-white" /></div>}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={onClose} disabled={busy}>Cancel</Button>
          {!confirming ? <Button type="button" onClick={() => void showPreview()} disabled={!ready || busy}>Preview email</Button>
            : <Button type="button" onClick={() => void send()} disabled={busy}><Send className="size-4" /> {busy ? "Queueing…" : `Confirm and send to ${recipientIds.length}`}</Button>}
        </div>
      </div>
    </DialogContent>
  </Dialog>;
}
