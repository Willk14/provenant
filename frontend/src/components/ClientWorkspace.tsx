"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { ChecksTab } from "@/components/ChecksTab";
import { EtimsTab } from "@/components/EtimsTab";
import { MappingTab } from "@/components/MappingTab";
import { RecordsTab } from "@/components/RecordsTab";
import { StatementsTab } from "@/components/StatementsTab";
import { TaxTab } from "@/components/TaxTab";
import { formatDate } from "@/lib/format";
import { taxApi, type Client } from "@/lib/tax";
import { usePersona } from "@/lib/usePersona";

type Tab = "mapping" | "records" | "statements" | "etims" | "checks" | "tax";

export function ClientWorkspace({ id }: { id: string }) {
  const persona = usePersona();
  const [client, setClient] = useState<Client | null>(null);
  const [yearEnd, setYearEnd] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("mapping");
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(0); // bumps when mappings change, so the tax tab recomputes

  const load = useCallback(async () => {
    try {
      const found = (await taxApi.clients()).find((c) => c.id === id) ?? null;
      setClient(found);
      setYearEnd((current) => current ?? found?.year_ends[0] ?? null);
      if (!found) setError("Client not found.");
    } catch (e) {
      setError((e as Error).message);
    }
  }, [id]);

  useEffect(() => {
    if (persona) void load();
  }, [persona, load]);

  return (
    <main className="page" style={{ maxWidth: 1320 }}>
      <div className="page-head">
        <div>
          <Link href="/clients" className="muted" style={{ fontSize: 13 }}>
            ← Clients &amp; tax
          </Link>
          <h1>{client?.name ?? "Loading…"}</h1>
          <p className="muted">Kenya corporation tax · Income Tax Act · rules ke-2026.4</p>
        </div>
        {client && (
          <div className="year-picker">
            <label htmlFor="year" className="muted" style={{ fontSize: 13 }}>
              Financial year
            </label>
            <select
              id="year"
              className="input"
              value={yearEnd ?? ""}
              onChange={(e) => setYearEnd(e.target.value)}
              disabled={!client.year_ends.length}
            >
              {client.year_ends.length === 0 && <option value="">No years yet</option>}
              {client.year_ends.map((y) => (
                <option key={y} value={y}>
                  Year ended {formatDate(y)}
                </option>
              ))}
            </select>
          </div>
        )}
      </div>

      {error && <div className="error-banner">{error}</div>}
      {client && (
        <UploadTrialBalance
          clientId={id}
          onUploaded={async (y) => {
            setYearEnd(y);
            await load();
          }}
        />
      )}

      {client && yearEnd && (
        <>
          <div className="tabs page-tabs" role="tablist">
            <button role="tab" aria-selected={tab === "mapping"} onClick={() => setTab("mapping")}>
              Financial mapping
            </button>
            <button role="tab" aria-selected={tab === "records"} onClick={() => setTab("records")}>
              Records &amp; inputs
            </button>
            <button role="tab" aria-selected={tab === "statements"} onClick={() => setTab("statements")}>
              Statements
            </button>
            <button role="tab" aria-selected={tab === "etims"} onClick={() => setTab("etims")}>
              eTIMS
            </button>
            <button role="tab" aria-selected={tab === "checks"} onClick={() => setTab("checks")}>
              Checks
            </button>
            <button role="tab" aria-selected={tab === "tax"} onClick={() => setTab("tax")}>
              Tax computation
            </button>
          </div>
          {tab === "mapping" ? (
            <MappingTab clientId={id} yearEnd={yearEnd} onChanged={() => setVersion((v) => v + 1)} />
          ) : tab === "statements" ? (
            <StatementsTab key={yearEnd} clientId={id} yearEnd={yearEnd} />
          ) : tab === "checks" ? (
            <ChecksTab key={yearEnd} clientId={id} yearEnd={yearEnd} />
          ) : tab === "etims" ? (
            <EtimsTab key={yearEnd} clientId={id} yearEnd={yearEnd} onChanged={() => setVersion((v) => v + 1)}
              onOpenComputation={() => setTab("tax")} />
          ) : tab === "records" ? (
            <RecordsTab
              key={yearEnd}
              clientId={id}
              yearEnd={yearEnd}
              onSaved={() => setVersion((v) => v + 1)}
              onOpenComputation={() => setTab("tax")}
            />
          ) : (
            <TaxTab
              key={`${yearEnd}-${version}`}
              clientId={id}
              yearEnd={yearEnd}
              onOpenMapping={() => setTab("mapping")}
              onOpenRecords={() => setTab("records")}
            />
          )}
        </>
      )}
    </main>
  );
}

function UploadTrialBalance({ clientId, onUploaded }: { clientId: string; onUploaded: (yearEnd: string) => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [yearEnd, setYearEnd] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const file = input.current?.files?.[0];
    if (!file || !yearEnd) {
      setError("Choose the financial year end and a CSV file.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await taxApi.uploadTrialBalance(clientId, yearEnd, file);
      if (input.current) input.current.value = "";
      onUploaded(yearEnd);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="dropzone" onSubmit={submit}>
      <div className="dropzone-icon" aria-hidden>
        ⤒
      </div>
      <div style={{ flex: 1 }}>
        <strong>Upload a year&apos;s trial balance</strong>
        <div className="muted" style={{ fontSize: 13 }}>
          CSV with <span className="mono">code, name, balance</span> (debit positive) or{" "}
          <span className="mono">debit, credit</span>. It must balance to the cent. Accounts seen before keep their
          confirmed mapping.
        </div>
        {error && <div className="term-error">{error}</div>}
      </div>
      <input
        className="input"
        type="date"
        value={yearEnd}
        onChange={(e) => setYearEnd(e.target.value)}
        aria-label="Financial year end"
      />
      <input ref={input} type="file" accept=".csv,text/csv" aria-label="Trial balance CSV" style={{ fontSize: 12 }} />
      <button className="btn" disabled={busy}>
        {busy ? "Uploading…" : "Upload"}
      </button>
    </form>
  );
}
