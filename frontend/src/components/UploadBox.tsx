"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";
import { formatDate } from "@/lib/format";
import {
  checksApi,
  etimsApi,
  ledgerApi,
  statementsApi,
  taxApi,
  type Client,
} from "@/lib/tax";
import type { DocumentSummary } from "@/lib/types";

type Need = "client" | "year" | "newYear" | "label" | "category" | "limit" | "agreement" | "periodEnd";

interface Kind {
  label: string;
  group: "Contracts" | "Client records" | "Evidence";
  accept: string;
  hint: string;
  needs: Need[];
  multiple?: boolean;
  tab?: string; // the client tab that shows the result
}

const TABLE = ".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
const ANY = ".pdf,.png,.jpg,.jpeg,.xlsx,.csv";

export const KINDS = {
  lease: { label: "Lease", group: "Contracts", accept: "application/pdf", multiple: true, needs: [],
    hint: "PDF. Terms are read automatically; amendments are uploaded from their lease." },
  credit_agreement: { label: "Credit agreement", group: "Contracts", accept: "application/pdf", multiple: true, needs: [],
    hint: "PDF. Financial covenants are read automatically." },
  covenant_tb: { label: "Quarterly trial balance (covenants)", group: "Contracts", accept: TABLE,
    needs: ["agreement", "periodEnd"], hint: "CSV or Excel: code, name, category, balance. Tested against the agreement." },
  trial_balance: { label: "Year-end trial balance", group: "Client records", accept: TABLE, needs: ["client", "newYear"],
    tab: "mapping", hint: "CSV or Excel: code, name, balance (debit positive). Starts a client's year." },
  fixed_assets: { label: "Fixed asset register", group: "Client records", accept: TABLE, needs: ["client", "year"],
    tab: "records", hint: "CSV or Excel: asset_code, description, class, acquired, cost; optional disposed, proceeds." },
  general_ledger: { label: "General ledger", group: "Client records", accept: TABLE, needs: ["client", "year", "limit"],
    tab: "ledger", hint: "CSV or Excel: entry_id, date, account_code, amount (or debit, credit). Tested in full." },
  mpesa: { label: "M-Pesa statement", group: "Client records", accept: TABLE, needs: ["client", "year", "label"],
    tab: "statements", hint: "The M-Pesa business portal's export (CSV or Excel)." },
  bank: { label: "Bank statement", group: "Client records", accept: TABLE, needs: ["client", "year", "label"],
    tab: "statements", hint: "CSV or Excel: date, description, and amount (or debit, credit)." },
  purchase_ledger: { label: "Purchase ledger (eTIMS)", group: "Client records", accept: TABLE, needs: ["client", "year"],
    tab: "etims", hint: "CSV or Excel: date, supplier, supplier_pin, invoice_number, account_code, amount." },
  etims_invoices: { label: "eTIMS purchase invoices", group: "Client records", accept: TABLE, needs: ["client", "year"],
    tab: "etims", hint: "The eTIMS export: cu_invoice_number, supplier_pin, invoice_number, date, taxable_value." },
  vat: { label: "VAT returns", group: "Client records", accept: TABLE, needs: ["client", "year"], tab: "checks",
    hint: "CSV or Excel: period, standard_rated_sales, zero_rated_sales, exempt_sales." },
  paye: { label: "PAYE (P10) returns", group: "Client records", accept: TABLE, needs: ["client", "year"], tab: "checks",
    hint: "CSV or Excel: period, gross_pay, paye." },
  wht: { label: "Withholding tax certificates issued", group: "Client records", accept: TABLE, needs: ["client", "year"],
    tab: "checks", hint: "CSV or Excel: certificate_number, withholdee_pin, date, gross_amount, wht_amount." },
  supporting: { label: "Supporting document", group: "Evidence", accept: ANY, needs: ["client", "year", "category"],
    tab: "records", hint: "PDF, image, Excel or CSV up to 25 MB: kept as evidence (bank statements, KRA certificates, receipts)." },
} satisfies Record<string, Kind>;

export type KindKey = keyof typeof KINDS;
const GROUPS = ["Contracts", "Client records", "Evidence"] as const;
const NEW_CLIENT = "__new__";

export function UploadBox({ agreements, onContractsChanged }: {
  agreements: DocumentSummary[];
  onContractsChanged: () => Promise<void>;
}) {
  const [kind, setKind] = useState<KindKey>("lease");
  const [clients, setClients] = useState<Client[]>([]);
  const [clientId, setClientId] = useState("");
  const [newClient, setNewClient] = useState("");
  const [year, setYear] = useState("");
  const [newYear, setNewYear] = useState("");
  const [label, setLabel] = useState("");
  const [limit, setLimit] = useState("");
  const [categories, setCategories] = useState<Record<string, string>>({});
  const [category, setCategory] = useState("withholding_certificate");
  const [agreement, setAgreement] = useState("");
  const [periodEnd, setPeriodEnd] = useState("");
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<{ text: string; href?: string; link?: string } | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const spec: Kind = KINDS[kind];
  const needs = new Set<Need>(spec.needs);
  const client = clients.find((c) => c.id === clientId) ?? null;

  const loadClients = useCallback(async () => {
    try {
      const list = await taxApi.clients();
      setClients(list);
      setClientId((current) => current || list[0]?.id || "");
    } catch {
      // the documents list shows connection errors
    }
  }, []);

  useEffect(() => {
    void loadClients();
    taxApi.meta().then((m) => setCategories(m.attachment_categories)).catch(() => undefined);
  }, [loadClients]);

  useEffect(() => {
    setYear((current) => (client && client.year_ends.includes(current) ? current : client?.year_ends[0] ?? ""));
  }, [client]);

  useEffect(() => {
    setAgreement((current) => current || agreements[0]?.id || "");
  }, [agreements]);

  function missing(): string | null {
    if (needs.has("client") && !clientId) return "Choose a client.";
    if (needs.has("newYear") && clientId === NEW_CLIENT && newClient.trim().length < 2) return "Name the new client.";
    if (needs.has("newYear") && !newYear) return "Enter the financial year end.";
    if (needs.has("year") && (clientId === NEW_CLIENT || !year))
      return "This needs a client year: upload the year-end trial balance first.";
    if (needs.has("label") && label.trim().length < 3) return "Name the account, e.g. 'M-Pesa Till 123456'.";
    if (needs.has("agreement") && !agreement) return "Upload the credit agreement first.";
    if (needs.has("periodEnd") && !periodEnd) return "Enter the quarter end.";
    return null;
  }

  async function send(file: File): Promise<{ text: string; href?: string; link?: string }> {
    const tabLink = (id: string, ye: string) =>
      ({ href: `/clients/${id}?tab=${spec.tab}&year=${ye}`, link: `Open ${KINDS[kind].label.toLowerCase()} in the client` });
    switch (kind) {
      case "lease":
      case "credit_agreement":
        await api.upload(file, undefined, kind);
        await onContractsChanged();
        return { text: `${file.name} uploaded and being read.` };
      case "covenant_tb":
        await api.uploadTrialBalance(agreement, file, periodEnd);
        return { text: `${file.name} attached for the quarter to ${formatDate(periodEnd)}.`,
          href: `/documents/${agreement}?tab=schedule`, link: "Open the covenant results" };
      case "trial_balance": {
        let id = clientId;
        if (id === NEW_CLIENT) id = (await taxApi.createClient(newClient.trim())).id;
        await taxApi.uploadTrialBalance(id, newYear, file);
        await loadClients();
        setClientId(id);
        return { text: `${file.name} uploaded for the year to ${formatDate(newYear)}.`, ...tabLink(id, newYear) };
      }
      case "fixed_assets":
        await taxApi.uploadRegister(clientId, year, file);
        break;
      case "general_ledger":
        await ledgerApi.upload(clientId, year, file, limit);
        break;
      case "mpesa":
      case "bank":
        await statementsApi.upload(clientId, year, kind, label.trim(), file);
        break;
      case "purchase_ledger":
        await etimsApi.uploadLedger(clientId, year, file);
        break;
      case "etims_invoices":
        await etimsApi.uploadInvoices(clientId, year, file);
        break;
      case "vat":
      case "paye":
      case "wht":
        await checksApi.upload(clientId, year, kind, file);
        break;
      case "supporting":
        await taxApi.uploadAttachment(clientId, year, file, category, "", "");
        break;
    }
    return { text: `${file.name} uploaded to ${client?.name}, year to ${formatDate(year)}.`, ...tabLink(clientId, year) };
  }

  async function upload(files: FileList | null) {
    const chosen = Array.from(files ?? []);
    if (!chosen.length) return;
    const problem = missing();
    if (problem) {
      setError(problem);
      return;
    }
    setBusy(true);
    setError(null);
    setDone(null);
    try {
      let last = null;
      for (const file of spec.multiple ? chosen : chosen.slice(0, 1)) last = await send(file);
      setDone(last);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  }

  const choose = (next: KindKey) => {
    setKind(next);
    setError(null);
    setDone(null);
  };
  const inGroup = (g: (typeof GROUPS)[number]) => (Object.keys(KINDS) as KindKey[]).filter((k) => KINDS[k].group === g);

  return (
    <div className="upload-box">
      <div className="upload-types">
        <div className="tabs upload-groups" role="tablist" aria-label="Kind of document">
          {GROUPS.map((g) => (
            <button key={g} type="button" role="tab" aria-selected={spec.group === g}
              onClick={() => spec.group !== g && choose(inGroup(g)[0])}>
              {g} <span className="tab-count">{inGroup(g).length}</span>
            </button>
          ))}
        </div>
        <div className="upload-kinds" role="group" aria-label={`${spec.group} you can upload`}>
          {inGroup(spec.group).map((k) => (
            <button key={k} type="button" className="kind-chip" aria-pressed={kind === k} onClick={() => choose(k)}>
              {KINDS[k].label}
            </button>
          ))}
        </div>
      </div>
      <div
        className="dropzone"
        data-over={over}
        onDragOver={(e) => {
          e.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setOver(false);
          void upload(e.dataTransfer.files);
        }}
      >
        <div className="dropzone-icon" aria-hidden>⤒</div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <strong>Drop {spec.multiple ? `${spec.label.toLowerCase()} PDFs` : `a ${spec.label.toLowerCase()}`} here</strong>
          <div className="muted" style={{ fontSize: 13 }}>{spec.hint}</div>
        </div>
        <input ref={input} type="file" accept={spec.accept} multiple={spec.multiple} hidden
          onChange={(e) => void upload(e.target.files)} />
        <button className="btn btn-primary" disabled={busy} onClick={() => input.current?.click()}>
          {busy ? "Uploading…" : spec.multiple ? "Choose files" : "Choose file"}
        </button>
      </div>

      {spec.needs.length > 0 && (
        <div className="upload-fields">
          {needs.has("client") && (
            <label className="field">
              <span>Client</span>
              <select className="input" value={clientId} onChange={(e) => setClientId(e.target.value)}>
                {clients.length === 0 && <option value="">No clients yet</option>}
                {clients.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                {needs.has("newYear") && <option value={NEW_CLIENT}>New client…</option>}
              </select>
            </label>
          )}
          {needs.has("newYear") && clientId === NEW_CLIENT && (
            <label className="field">
              <span>New client name</span>
              <input className="input" value={newClient} onChange={(e) => setNewClient(e.target.value)}
                placeholder="e.g. Baobab Logistics Ltd" />
            </label>
          )}
          {needs.has("newYear") && (
            <label className="field">
              <span>Financial year end</span>
              <input className="input" type="date" value={newYear} onChange={(e) => setNewYear(e.target.value)} />
            </label>
          )}
          {needs.has("year") && (
            <label className="field">
              <span>Year</span>
              <select className="input" value={year} onChange={(e) => setYear(e.target.value)}
                disabled={!client || client.year_ends.length === 0}>
                {(!client || client.year_ends.length === 0) && <option value="">No years: upload a trial balance</option>}
                {client?.year_ends.map((y) => <option key={y} value={y}>Year ended {formatDate(y)}</option>)}
              </select>
            </label>
          )}
          {needs.has("label") && (
            <label className="field">
              <span>Account name</span>
              <input className="input" value={label} onChange={(e) => setLabel(e.target.value)}
                placeholder={kind === "mpesa" ? "e.g. M-Pesa Till 123456" : "e.g. Equity Bank current account"} />
            </label>
          )}
          {needs.has("limit") && (
            <label className="field">
              <span>Approval limit (optional)</span>
              <input className="input" value={limit} onChange={(e) => setLimit(e.target.value)} placeholder="e.g. 5,000,000" />
            </label>
          )}
          {needs.has("category") && (
            <label className="field">
              <span>Kind of document</span>
              <select className="input" value={category} onChange={(e) => setCategory(e.target.value)}>
                {Object.entries(categories).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
            </label>
          )}
          {needs.has("agreement") && (
            <label className="field">
              <span>Credit agreement</span>
              <select className="input" value={agreement} onChange={(e) => setAgreement(e.target.value)}>
                {agreements.length === 0 && <option value="">No credit agreements yet</option>}
                {agreements.map((a) => <option key={a.id} value={a.id}>{a.filename}</option>)}
              </select>
            </label>
          )}
          {needs.has("periodEnd") && (
            <label className="field">
              <span>Quarter end</span>
              <input className="input" type="date" value={periodEnd} onChange={(e) => setPeriodEnd(e.target.value)} />
            </label>
          )}
        </div>
      )}

      {error && <div className="error-banner">{error}</div>}
      {done && (
        <div className="callout callout-ok" style={{ fontSize: 13 }}>
          {done.text}{" "}
          {done.href && <Link className="link" href={done.href}>{done.link} →</Link>}
        </div>
      )}
    </div>
  );
}
