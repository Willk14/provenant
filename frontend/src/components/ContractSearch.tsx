"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { api } from "@/lib/api";
import type { DocumentSummary, LegalCitation, LegalIndexState, LegalSearchHit } from "@/lib/types";

export function ContractSearch({ doc, onCite }: {
  doc: DocumentSummary;
  onCite: (id: string, citation: LegalCitation) => void;
}) {
  const [index, setIndex] = useState<LegalIndexState | null>(null);
  const [query, setQuery] = useState("");
  const [everywhere, setEverywhere] = useState(false);
  const [hits, setHits] = useState<LegalSearchHit[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadIndex = useCallback(async () => {
    try {
      setIndex(await api.legalIndex());
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    void loadIndex();
  }, [loadIndex]);

  const [building, setBuilding] = useState(false);
  const missing = index ? index.documents - index.ready : 0;
  const matchWords = { vector: index?.semantic ? "similar meaning" : "similar words", keyword: "same words" };
  useEffect(() => {
    if (!building) return;
    if (index && (missing === 0 || index.failed > 0)) {
      setBuilding(false);
      return;
    }
    const timer = setInterval(loadIndex, 1200);
    return () => clearInterval(timer);
  }, [building, index, missing, loadIndex]);

  async function search() {
    setBusy(true);
    setError(null);
    try {
      setHits(await api.searchContracts(query, everywhere ? undefined : doc.id));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function build() {
    setError(null);
    try {
      setIndex(await api.buildLegalIndex());
      setBuilding(true);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <div className="term" style={{ cursor: "default", marginTop: 14 }}>
      <div className="term-head">
        <span className="term-label">Search contracts</span>
        <span className="faint" style={{ fontSize: 12 }}>
          {index ? `${index.ready} of ${index.documents} indexed` : ""}
        </span>
      </div>
      {index && !index.semantic && (
        <div className="term-note">
          Local index: matches shared and similar words. It may miss a clause written in different words.
        </div>
      )}
      {index && index.failed > 0 && <div className="error-banner">{index.failed} could not be indexed.</div>}
      {missing > 0 && (
        <button className="btn btn-sm" disabled={building} onClick={() => void build()}>
          {building ? "Indexing…" : `Index ${missing} document${missing === 1 ? "" : "s"}`}
        </button>
      )}
      <form className="term-actions" style={{ flexWrap: "wrap" }} onSubmit={(e) => {
        e.preventDefault();
        void search();
      }}>
        <input className="input" value={query} onChange={(e) => setQuery(e.target.value)}
          placeholder="Termination on change of control" aria-label="Search contracts" />
        <label className="faint" style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 12 }}>
          <input type="checkbox" checked={everywhere} onChange={(e) => setEverywhere(e.target.checked)} /> All contracts
        </label>
        <button className="btn btn-sm" disabled={busy || query.trim().length < 3}>Search</button>
      </form>
      {error && <div className="error-banner">{error}</div>}
      {hits?.length === 0 && (
        <div className="term-note">No passages found. That does not prove the contract is silent on this.</div>
      )}
      {hits?.map((hit, i) => {
        const here = hit.citation.document_id === doc.id;
        return (
          <div key={`${hit.citation.document_id}-${i}`} style={{ marginTop: 10 }}>
            <div className="term-note" style={{ whiteSpace: "pre-wrap" }}>{hit.text}</div>
            <div className="chips">
              {here ? (
                <button className="chip" title="Show on the PDF" onClick={() => onCite(`search-${i}`, hit.citation)}>
                  <span className="faint">Page</span> <b>{hit.citation.page}</b>
                </button>
              ) : (
                <Link className="chip" href={`/documents/${hit.citation.document_id}`}>
                  <span className="faint">{hit.citation.filename}, page</span> <b>{hit.citation.page}</b>
                </Link>
              )}
              <span className="faint" style={{ fontSize: 12 }}>{hit.matched.map((m) => matchWords[m]).join(" + ")}</span>
            </div>
          </div>
        );
      })}
    </div>
  );
}
