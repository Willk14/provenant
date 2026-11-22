"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { DocumentStatusPill } from "@/components/StatusPill";
import { UploadBox } from "@/components/UploadBox";
import { api } from "@/lib/api";
import { formatBytes, formatDate } from "@/lib/format";
import type { DocumentSummary } from "@/lib/types";
import { usePersona } from "@/lib/usePersona";

/** Leases newest first, each followed by its amendments in upload order. */
export function withAmendmentsUnderLeases(docs: DocumentSummary[]): DocumentSummary[] {
  const children = new Map<string, DocumentSummary[]>();
  for (const d of docs) {
    if (d.parent_id) children.set(d.parent_id, [...(children.get(d.parent_id) ?? []), d]);
  }
  const byCreated = (a: DocumentSummary, b: DocumentSummary) => a.created_at.localeCompare(b.created_at);
  const out: DocumentSummary[] = [];
  for (const d of docs) {
    if (d.kind === "lease") out.push(d, ...(children.get(d.id) ?? []).sort(byCreated));
  }
  const listed = new Set(out.map((d) => d.id));
  return [...out, ...docs.filter((d) => !listed.has(d.id))]; // amendments whose lease is beyond the page
}

export function DocumentsView() {
  const router = useRouter();
  const persona = usePersona();
  const [docs, setDocs] = useState<DocumentSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setDocs(await api.documents());
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    if (persona) void load();
  }, [persona, load]);

  // Poll while anything is still being extracted.
  const busy = docs?.some((d) => d.status === "queued" || d.status === "extracting");
  useEffect(() => {
    if (!busy) return;
    const timer = setInterval(load, 1500);
    return () => clearInterval(timer);
  }, [busy, load]);

  return (
    <main className="page">
      <div className="page-head">
        <div>
          <h1>Documents</h1>
          <p className="muted">
            Upload contracts and client records here: leases and credit agreements are read automatically, client
            records go to the client&apos;s year. Every figure keeps a link to where it came from.
          </p>
        </div>
      </div>

      <UploadBox
        agreements={(docs ?? []).filter((d) => d.kind === "credit_agreement")}
        onContractsChanged={load}
      />

      {error && <div className="error-banner">{error}</div>}

      <div className="card">
        {docs === null ? (
          <div className="empty">Loading…</div>
        ) : docs.length === 0 ? (
          <div className="empty">
            No documents yet. Upload a lease, or run <span className="mono">python scripts/seed_demo.py</span> for two
            samples.
          </div>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Document</th>
                <th>Status</th>
                <th>Uploaded by</th>
                <th>Uploaded</th>
                <th style={{ textAlign: "right" }}>Size</th>
              </tr>
            </thead>
            <tbody>
              {withAmendmentsUnderLeases(docs).map((d) => (
                <tr
                  key={d.id}
                  className="link-row"
                  data-amendment={d.kind === "amendment" || undefined}
                  onClick={() => router.push(`/documents/${d.id}`)}
                >
                  <td>
                    {d.kind === "amendment" && <span className="kind-tag">Amendment</span>}
                    {d.kind === "credit_agreement" && <span className="kind-tag kind-credit">Credit agreement</span>}
                    <a href={`/documents/${d.id}`} onClick={(e) => e.preventDefault()} style={{ fontWeight: 550 }}>
                      {d.filename}
                    </a>
                    {d.error && <div className="term-error">{d.error}</div>}
                  </td>
                  <td>
                    <DocumentStatusPill status={d.status} needsReview={d.needs_review} missing={d.missing_terms} />
                  </td>
                  <td className="muted">{d.uploaded_by}</td>
                  <td className="muted num">{formatDate(d.created_at)}</td>
                  <td className="muted num" style={{ textAlign: "right" }}>
                    {formatBytes(d.size_bytes)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </main>
  );
}
