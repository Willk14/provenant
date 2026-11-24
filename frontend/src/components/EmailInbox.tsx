"use client";

import { useCallback, useEffect, useState } from "react";

import { api, ApiError } from "@/lib/api";
import { formatBytes, formatDate } from "@/lib/format";
import type { Inbox } from "@/lib/types";

/** Attachments emailed to the firm's address. Nothing is read until a person files it. */
export function EmailInbox({ onFiled }: { onFiled: () => Promise<void> }) {
  const [inbox, setInbox] = useState<Inbox | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const load = useCallback(async () => {
    try {
      setInbox(await api.inbox());
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 404)) setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function act(action: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await action();
      await load();
      await onFiled();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function download(id: string, filename: string, type: string) {
    const bytes = await api.inboxFile(id);
    const url = URL.createObjectURL(new Blob([bytes], { type }));
    Object.assign(document.createElement("a"), { href: url, download: filename }).click();
    URL.revokeObjectURL(url);
  }

  if (!inbox || (!inbox.address && inbox.items.length === 0)) return null;
  return (
    <section className="card email-inbox" aria-label="Email inbox">
      <div className="email-inbox-head">
        <div>
          <strong>Email inbox</strong>
          <div className="faint" style={{ fontSize: 12 }}>
            Forward contracts and records to this address from your work email. They wait here until someone files them.
          </div>
        </div>
        {inbox.address && (
          <button type="button" className="btn btn-sm mono email-address" title="Copy the address"
            onClick={() => void navigator.clipboard.writeText(inbox.address ?? "").then(() => setCopied(true))}>
            {copied ? "Copied" : inbox.address}
          </button>
        )}
      </div>
      {error && <div className="error-banner">{error}</div>}
      {inbox.items.length === 0 ? (
        <div className="faint" style={{ fontSize: 13 }}>Nothing waiting.</div>
      ) : (
        <ul className="email-items">
          {inbox.items.map((item) => (
            <li key={item.id}>
              <div className="email-item-name">
                <b>{item.filename}</b>
                <span className="faint">
                  {item.sender} · {formatBytes(item.size_bytes)} · {formatDate(item.received_at)}
                  {item.subject ? ` · “${item.subject}”` : ""}
                </span>
              </div>
              <div className="email-item-actions">
                {item.content_type === "application/pdf" && (
                  <>
                    <button className="btn btn-sm" disabled={busy}
                      onClick={() => void act(() => api.fileInboxItem(item.id, "lease"))}>File as lease</button>
                    <button className="btn btn-sm" disabled={busy}
                      onClick={() => void act(() => api.fileInboxItem(item.id, "credit_agreement"))}>File as credit agreement</button>
                  </>
                )}
                <button className="btn btn-sm btn-ghost" disabled={busy}
                  onClick={() => void download(item.id, item.filename, item.content_type).catch((e: Error) => setError(e.message))}>
                  Download
                </button>
                <button className="btn btn-sm btn-ghost" disabled={busy}
                  onClick={() => void act(() => api.dismissInboxItem(item.id))}>Dismiss</button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
