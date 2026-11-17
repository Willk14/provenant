"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { displayValue } from "@/components/TermsPanel";
import { api } from "@/lib/api";
import { FIELD_LABELS, JUDGEMENT_FIELDS, type DocumentSummary, type Field } from "@/lib/types";
import { usePersona } from "@/lib/usePersona";

export default function ReviewQueuePage() {
  const persona = usePersona();
  const [queue, setQueue] = useState<Field[] | null>(null);
  const [docs, setDocs] = useState<Map<string, DocumentSummary>>(new Map());
  const [log, setLog] = useState<{ intact: boolean; events: number } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!persona) return;
    Promise.all([api.reviewQueue(), api.documents(), api.verifyLog()])
      .then(([fields, documents, verified]) => {
        setQueue(fields);
        setDocs(new Map(documents.map((d) => [d.id, d])));
        setLog(verified);
      })
      .catch((e: Error) => setError(e.message));
  }, [persona]);

  const byDoc = new Map<string, Field[]>();
  for (const f of queue ?? []) byDoc.set(f.document_id, [...(byDoc.get(f.document_id) ?? []), f]);

  return (
    <main className="page">
      <div className="page-head">
        <div>
          <h1>Review queue</h1>
          <p className="muted">
            Figures waiting for a person: low-confidence reads, judgement calls, and values typed in by hand.
          </p>
        </div>
        {log && (
          <span className={`pill ${log.intact ? "pill-ok" : "pill-bad"}`} title="Every review is hash-chained">
            Review log {log.intact ? "intact" : "TAMPERED"} · {log.events} decisions
          </span>
        )}
      </div>
      {error && <div className="error-banner">{error}</div>}
      <div className="card">
        {queue === null ? (
          <div className="empty">Loading…</div>
        ) : queue.length === 0 ? (
          <div className="empty">Nothing waiting for review.</div>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Term</th>
                <th>Proposed value</th>
                <th>Source</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {[...byDoc.entries()].flatMap(([docId, fields]) => [
                <tr key={docId}>
                  <td colSpan={4} style={{ background: "var(--surface-2)", fontWeight: 600, padding: "8px 16px" }}>
                    {docs.get(docId)?.filename ?? docId}
                  </td>
                </tr>,
                ...fields.map((f) => (
                  <tr key={f.id}>
                    <td>
                      {FIELD_LABELS[f.name]}{" "}
                      {JUDGEMENT_FIELDS.has(f.name) && <span className="judgement">Partner</span>}
                    </td>
                    <td className="num" style={{ fontWeight: 600 }}>
                      {displayValue(f)}
                    </td>
                    <td className="muted">
                      Page {f.page} · {f.extractor === "manual" ? `entered by ${f.entered_by}` : `${Math.round(f.confidence * 100)}%`}
                    </td>
                    <td style={{ textAlign: "right" }}>
                      <Link className="btn btn-sm" href={`/documents/${docId}?field=${f.name}`}>
                        Open in document
                      </Link>
                    </td>
                  </tr>
                )),
              ])}
            </tbody>
          </table>
        )}
      </div>
    </main>
  );
}
