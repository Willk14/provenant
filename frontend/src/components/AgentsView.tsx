"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { ContractChat } from "@/components/ContractChat";
import { api, ApiError } from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { AiAgents, AiUsage } from "@/lib/types";
import { usePersona } from "@/lib/usePersona";

const ENGINE_PILL = { claude: "pill-ok", keyword: "pill-warn", needs_setup: "pill-bad" } as const;
const ENGINE_WORD = { claude: "Live", keyword: "Free mode", needs_setup: "Needs setup" } as const;
const ACTIVITY = { review: "Review", question: "Question", draft: "Draft" } as const;
const STATUS_PILL: Record<string, string> = {
  done: "pill-ok", approved: "pill-ok", ready: "pill-warn", returned: "pill-warn", failed: "pill-bad",
};
const STATUS_WORD: Record<string, string> = {
  done: "Done", approved: "Approved", ready: "To review", returned: "Returned", failed: "Failed", queued: "Queued",
  running: "Working",
};
const WAITING = { reviewer: "to decide", questions: "", drafter: "to finish" } as const;
const ACTION = { reviewer: "Review a contract", questions: "Ask a question", drafter: "Prepare a draft" } as const;

/** Integer micro-dollars to a display string, without passing through a float. */
export function formatUsd(micro: number): string {
  const whole = Math.trunc(micro / 1_000_000);
  return `USD ${whole}.${String(micro % 1_000_000).padStart(6, "0").slice(0, 4)}`;
}

export function AgentsView() {
  const persona = usePersona();
  const router = useRouter();
  const [data, setData] = useState<AiAgents | null>(null);
  const [usage, setUsage] = useState<AiUsage | null>(null);
  const [contract, setContract] = useState("");
  const [restart, setRestart] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const partner = !!persona?.roles.includes("partner");

  const load = useCallback(async () => {
    try {
      const next = await api.aiAgents();
      setData(next);
      setContract((current) => current || next.contracts[0]?.id || "");
      setRestart(false);
      setError(null);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) setRestart(true);
      else setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    if (persona) void load();
  }, [persona, load]);

  useEffect(() => {
    if (!partner) {
      setUsage(null);
      return;
    }
    api.aiUsage().then(setUsage).catch(() => setUsage(null));
  }, [partner]);

  const working = !!data?.activity.some((a) => a.status === "queued" || a.status === "running");
  useEffect(() => {
    if (!working) return;
    const timer = setInterval(load, 2000);
    return () => clearInterval(timer);
  }, [working, load]);

  return (
    <main className="page">
      <div className="page-head">
        <div>
          <h1>AI agents</h1>
          <p className="muted">
            Agents prepare cited work from your contracts. A qualified person decides what it means and approves it.
          </p>
        </div>
      </div>

      {error && <div className="error-banner">{error}</div>}
      {restart && (
        <div className="callout callout-warn">
          <strong>Restart the API to turn this page on</strong>
          The API running now started before the AI agents page was added.
        </div>
      )}
      {!data && !restart && !error && <div className="empty">Loading…</div>}

      {data && (
        <>
          <section className="card agent-engine">
            <div>
              <div className="agent-engine-title">
                <strong>{data.engine.label}</strong>
                <span className={`pill ${ENGINE_PILL[data.engine.mode]}`}>{ENGINE_WORD[data.engine.mode]}</span>
              </div>
              <div className="muted">{data.engine.detail}</div>
              {data.engine.model && <div className="faint mono" style={{ fontSize: 12 }}>{data.engine.model}</div>}
            </div>
            {usage && (
              <div className="agent-usage">
                <b className="num">{formatUsd(usage.estimated_cost_microusd)}</b>
                <span className="faint">estimated, {usage.total_runs} Claude run{usage.total_runs === 1 ? "" : "s"}</span>
                {usage.unpriced_runs > 0 && <span className="faint">{usage.unpriced_runs} not priced</span>}
              </div>
            )}
          </section>

          <div className="agent-picker">
            <label htmlFor="agent-contract">Contract to work on</label>
            <select id="agent-contract" className="input" value={contract} disabled={!data.contracts.length}
              onChange={(e) => setContract(e.target.value)}>
              {!data.contracts.length && <option value="">No contracts ready yet</option>}
              {data.contracts.map((c) => <option key={c.id} value={c.id}>{c.filename}</option>)}
            </select>
          </div>

          {contract && (
            <ContractChat contractId={contract} mode={data.engine.mode}
              filename={data.contracts.find((c) => c.id === contract)?.filename ?? ""} />
          )}

          <div className="agent-grid">
            {data.agents.map((a) => (
              <article className="card agent-card" key={a.key}>
                <h2>{a.name}</h2>
                <p className="muted">{a.description}</p>
                <div className="agent-counts">
                  <span><b className="num">{a.runs}</b> run{a.runs === 1 ? "" : "s"}</span>
                  {WAITING[a.key] && <span><b className="num">{a.waiting}</b> {WAITING[a.key]}</span>}
                </div>
                {a.note && <div className="faint" style={{ fontSize: 12 }}>{a.note}</div>}
                <button className="btn btn-sm" disabled={!contract || !a.available}
                  onClick={() => router.push(`/documents/${contract}?tab=${a.tab}`)}>
                  {ACTION[a.key]}
                </button>
              </article>
            ))}
          </div>

          <h2 className="agent-section">Recent work</h2>
          <div className="card">
            {data.activity.length === 0 ? (
              <div className="empty">No agent work yet. Choose a contract above to start.</div>
            ) : (
              <table className="table">
                <thead>
                  <tr><th>Work</th><th>Contract</th><th>Status</th><th>By</th><th style={{ textAlign: "right" }}>Date</th></tr>
                </thead>
                <tbody>
                  {data.activity.map((a) => (
                    <tr key={`${a.kind}-${a.id}`}>
                      <td>
                        <Link href={`/documents/${a.document_id}?tab=${a.kind === "draft" ? "drafts" : "review"}`}
                          style={{ fontWeight: 550 }}>{a.title}</Link>
                        <div className="faint" style={{ fontSize: 12 }}>{ACTIVITY[a.kind]}{a.agent ? ` · ${a.agent}` : ""}</div>
                      </td>
                      <td className="muted">{a.filename}</td>
                      <td><span className={`pill ${STATUS_PILL[a.status] ?? ""}`}>{STATUS_WORD[a.status] ?? a.status}</span></td>
                      <td className="muted">{a.by}</td>
                      <td className="muted num" style={{ textAlign: "right" }}>{formatDate(a.at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </>
      )}
    </main>
  );
}
