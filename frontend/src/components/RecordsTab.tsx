"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "@/lib/api";
import { formatAmount, formatDate } from "@/lib/format";
import {
  taxApi,
  type Allowance,
  type Attachment,
  type Credit,
  type CreditKind,
  type Inputs,
  type RecordsMeta,
  type Register,
} from "@/lib/tax";

interface Props {
  clientId: string;
  yearEnd: string;
  onSaved: () => void;
  onOpenComputation: () => void;
}

const EMPTY_CLASS: Allowance = {
  name: "",
  wdv_brought_forward: "0",
  additions: "0",
  disposals: "0",
  rate_percent: "",
  method: "reducing",
  basis: "",
};

const EMPTY_CREDIT: Credit = { kind: "instalment", description: "", amount: "", reference: "", evidence: null };

function priorYearEnd(yearEnd: string): string {
  const d = new Date(`${yearEnd}T00:00:00Z`);
  const last = new Date(Date.UTC(d.getUTCFullYear() - 1, d.getUTCMonth() + 1, 0));
  return last.toISOString().slice(0, 10);
}

function kb(bytes: number): string {
  return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1048576).toFixed(1)} MB`;
}

export function RecordsTab({ clientId, yearEnd, onSaved, onOpenComputation }: Props) {
  const [meta, setMeta] = useState<RecordsMeta | null>(null);
  const [inputs, setInputs] = useState<Inputs | null>(null);
  const [register, setRegister] = useState<Register | null>(null);
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [m, i, r, a] = await Promise.all([
        taxApi.meta(),
        taxApi.inputs(clientId, yearEnd),
        taxApi.register(clientId, yearEnd),
        taxApi.attachments(clientId, yearEnd),
      ]);
      setMeta(m);
      setInputs(i);
      setRegister(r);
      setAttachments(a);
      setDirty(false);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [clientId, yearEnd]);

  useEffect(() => {
    void load();
  }, [load]);

  const edit = (patch: Partial<Inputs>) => {
    setInputs((cur) => cur && { ...cur, ...patch });
    setDirty(true);
    setNotice(null);
  };

  async function save() {
    if (!inputs) return;
    setSaving(true);
    setError(null);
    try {
      setInputs(await taxApi.saveInputs(clientId, yearEnd, inputs));
      setDirty(false);
      setNotice("Saved. The tax computation uses these figures now.");
      onSaved();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  async function rollForward() {
    setError(null);
    try {
      const proposal = await taxApi.rollForward(clientId, yearEnd);
      setInputs((cur) => ({ ...proposal, prior_return: cur?.prior_return ?? proposal.prior_return }));
      setDirty(true);
      setNotice(`Filled from the computation for the year ended ${formatDate(priorYearEnd(yearEnd))}. Review, then save.`);
    } catch (e) {
      setError(e instanceof ApiError && e.status === 409 ? `${e.message}.` : (e as Error).message);
    }
  }

  if (!inputs || !meta) {
    return error ? <div className="error-banner">{error}</div> : <div className="empty">Loading records…</div>;
  }

  const registerClass = (name: string) =>
    register?.classes.find((c) => c.asset_class.toLowerCase() === name.trim().toLowerCase());
  const setClass = (i: number, patch: Partial<Allowance>) =>
    edit({ allowances: inputs.allowances.map((a, j) => (j === i ? { ...a, ...patch } : a)) });
  const setCredit = (i: number, patch: Partial<Credit>) =>
    edit({ credits: inputs.credits.map((c, j) => (j === i ? { ...c, ...patch } : c)) });
  const unmatched = register?.classes.filter(
    (c) => !inputs.allowances.some((a) => a.name.trim().toLowerCase() === c.asset_class.toLowerCase()),
  );

  return (
    <div className="records-layout">
      <section className="records-main">
        <div className={`save-bar${dirty ? " save-bar-dirty" : ""}`}>
          <div>
            <strong>{dirty ? "Unsaved changes" : "Preparer inputs"}</strong>
            <div className="muted" style={{ fontSize: 13 }}>
              {notice ??
                (inputs.updated_by ? `Last saved by ${inputs.updated_by}.` : "Nothing saved for this year yet.")}
            </div>
          </div>
          <div className="term-actions" style={{ marginTop: 0 }}>
            <button className="btn btn-sm" type="button" onClick={onOpenComputation}>
              View computation
            </button>
            <button className="btn btn-primary btn-sm" type="button" disabled={!dirty || saving} onClick={save}>
              {saving ? "Saving…" : "Save inputs"}
            </button>
          </div>
        </div>
        {error && <div className="error-banner">{error}</div>}

        <div className="card record-card">
          <div className="record-head">
            <div>
              <h3>Prior-year return</h3>
              <p className="term-note">The opening position, as filed for the year ended {formatDate(priorYearEnd(yearEnd))}.</p>
            </div>
            <button className="btn btn-sm" type="button" onClick={rollForward}>
              Roll forward from last year
            </button>
          </div>
          <div className="record-grid">
            <label className="field">
              <span>Return acknowledgement no.</span>
              <input className="input" value={inputs.prior_return.reference} placeholder="e.g. KRA202512345678"
                onChange={(e) => edit({ prior_return: { ...inputs.prior_return, reference: e.target.value } })} />
            </label>
            <label className="field">
              <span>Filed on</span>
              <input className="input" type="date" value={inputs.prior_return.filed_on}
                onChange={(e) => edit({ prior_return: { ...inputs.prior_return, filed_on: e.target.value } })} />
            </label>
            <label className="field">
              <span>Tax losses brought forward</span>
              <input className="input num" value={inputs.losses_brought_forward}
                onChange={(e) => edit({ losses_brought_forward: e.target.value })} />
            </label>
          </div>
          <p className="term-note">
            Opening written-down values sit with each capital deduction class below. An overpayment brought forward is
            entered as a tax credit.
          </p>
        </div>

        <div className="card record-card">
          <div className="record-head">
            <div>
              <h3>Capital deduction classes</h3>
              <p className="term-note">
                Rates change with Finance Acts, so each class carries the rate you apply and its basis. Where the fixed
                asset register has a class of the same name, it supplies the additions and disposals.
              </p>
            </div>
            <button className="btn btn-sm" type="button"
              onClick={() => edit({ allowances: [...inputs.allowances, { ...EMPTY_CLASS }] })}>
              Add class
            </button>
          </div>
          {inputs.allowances.length === 0 && <div className="empty" style={{ padding: 16 }}>No classes yet.</div>}
          {inputs.allowances.map((a, i) => {
            const fromRegister = registerClass(a.name);
            return (
              <div key={i} className="record-row">
                <div className="record-grid record-grid-class">
                  <label className="field" style={{ gridColumn: "span 2" }}>
                    <span>Class</span>
                    <input className="input" value={a.name} placeholder="e.g. Plant and machinery"
                      onChange={(e) => setClass(i, { name: e.target.value })} />
                  </label>
                  <label className="field">
                    <span>Rate %</span>
                    <input className="input num" value={a.rate_percent} onChange={(e) => setClass(i, { rate_percent: e.target.value })} />
                  </label>
                  <label className="field">
                    <span>Method</span>
                    <select className="input" value={a.method}
                      onChange={(e) => setClass(i, { method: e.target.value as Allowance["method"] })}>
                      <option value="reducing">Reducing balance</option>
                      <option value="straight_line">Straight line</option>
                    </select>
                  </label>
                  <label className="field">
                    <span>WDV brought forward</span>
                    <input className="input num" value={a.wdv_brought_forward}
                      onChange={(e) => setClass(i, { wdv_brought_forward: e.target.value })} />
                  </label>
                  <label className="field">
                    <span>Additions{fromRegister && " · register"}</span>
                    <input className="input num" disabled={!!fromRegister}
                      value={fromRegister ? formatAmount(fromRegister.additions) : a.additions}
                      onChange={(e) => setClass(i, { additions: e.target.value })} />
                  </label>
                  <label className="field">
                    <span>Disposal proceeds{fromRegister && " · register"}</span>
                    <input className="input num" disabled={!!fromRegister}
                      value={fromRegister ? formatAmount(fromRegister.disposals) : (a.disposals ?? "0")}
                      onChange={(e) => setClass(i, { disposals: e.target.value })} />
                  </label>
                  {a.method === "straight_line" ? (
                    <label className="field">
                      <span>Qualifying cost</span>
                      <input className="input num" value={a.cost_basis ?? "0"}
                        onChange={(e) => setClass(i, { cost_basis: e.target.value })} />
                    </label>
                  ) : (
                    <span />
                  )}
                  <label className="field" style={{ gridColumn: "1 / -1" }}>
                    <span>Basis for the rate</span>
                    <input className="input" value={a.basis} placeholder="e.g. Second Schedule para. … (Finance Act year)"
                      onChange={(e) => setClass(i, { basis: e.target.value })} />
                  </label>
                </div>
                <button className="btn btn-sm btn-ghost btn-danger" type="button"
                  onClick={() => edit({ allowances: inputs.allowances.filter((_, j) => j !== i) })}>
                  Remove class
                </button>
              </div>
            );
          })}
          {unmatched && unmatched.length > 0 && (
            <div className="callout callout-warn" style={{ marginTop: 10, fontSize: 13 }}>
              The register has {unmatched.map((c) => c.asset_class).join(", ")} with no matching class here.{" "}
              <button className="btn btn-sm" type="button" style={{ marginLeft: 6 }}
                onClick={() => edit({ allowances: [...inputs.allowances, ...unmatched.map((c) => ({ ...EMPTY_CLASS, name: c.asset_class }))] })}>
                Add {unmatched.length === 1 ? "it" : "them"}
              </button>
            </div>
          )}
        </div>

        <div className="card record-card">
          <div className="record-head">
            <div>
              <h3>Tax credits</h3>
              <p className="term-note">
                Tax already paid or suffered for the year. Attach the receipt or certificate as evidence where you have it.
              </p>
            </div>
            <button className="btn btn-sm" type="button"
              onClick={() => edit({ credits: [...inputs.credits, { ...EMPTY_CREDIT }] })}>
              Add credit
            </button>
          </div>
          {inputs.credits.length === 0 ? (
            <div className="empty" style={{ padding: 16 }}>No credits entered.</div>
          ) : (
            <table className="table credit-table">
              <thead>
                <tr>
                  <th>Kind</th>
                  <th>Description</th>
                  <th>Reference</th>
                  <th>Evidence</th>
                  <th className="num">Amount</th>
                  <th aria-label="Remove" />
                </tr>
              </thead>
              <tbody>
                {inputs.credits.map((c, i) => (
                  <tr key={i}>
                    <td>
                      <select className="input" aria-label="Credit kind" value={c.kind}
                        onChange={(e) => setCredit(i, { kind: e.target.value as CreditKind })}>
                        {Object.entries(meta.credit_kinds).map(([k, label]) => (
                          <option key={k} value={k}>{label}</option>
                        ))}
                      </select>
                    </td>
                    <td>
                      <input className="input" aria-label="Description" value={c.description}
                        placeholder={c.kind === "withholding" ? "Payer, e.g. Safari Bank" : "e.g. 4th instalment"}
                        onChange={(e) => setCredit(i, { description: e.target.value })} />
                    </td>
                    <td>
                      <input className="input" aria-label="Reference" value={c.reference} placeholder="PRN / certificate no."
                        onChange={(e) => setCredit(i, { reference: e.target.value })} />
                    </td>
                    <td>
                      <select className="input" aria-label="Evidence" value={c.evidence ?? ""}
                        onChange={(e) => setCredit(i, { evidence: e.target.value || null })}>
                        <option value="">None</option>
                        {attachments.map((a) => (
                          <option key={a.id} value={a.id}>{a.filename}</option>
                        ))}
                      </select>
                    </td>
                    <td>
                      <input className="input num" aria-label="Amount" value={c.amount}
                        onChange={(e) => setCredit(i, { amount: e.target.value })} />
                    </td>
                    <td>
                      <button className="btn btn-sm btn-ghost btn-danger" type="button" aria-label="Remove credit"
                        onClick={() => edit({ credits: inputs.credits.filter((_, j) => j !== i) })}>
                        ×
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </section>

      <aside className="records-side">
        <RegisterCard clientId={clientId} yearEnd={yearEnd} register={register}
          onUploaded={(r) => { setRegister(r); onSaved(); }} />
        <AttachmentsCard clientId={clientId} yearEnd={yearEnd} meta={meta} attachments={attachments}
          usedAsEvidence={new Set(inputs.credits.map((c) => c.evidence).filter(Boolean) as string[])}
          onChanged={async () => setAttachments(await taxApi.attachments(clientId, yearEnd))} />
      </aside>
    </div>
  );
}

function RegisterCard({ clientId, yearEnd, register, onUploaded }: {
  clientId: string;
  yearEnd: string;
  register: Register | null;
  onUploaded: (r: Register) => void;
}) {
  const file = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showAssets, setShowAssets] = useState(false);

  async function upload() {
    const f = file.current?.files?.[0];
    if (!f) return setError("Choose the register CSV first.");
    setBusy(true);
    setError(null);
    try {
      onUploaded(await taxApi.uploadRegister(clientId, yearEnd, f));
      if (file.current) file.current.value = "";
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card record-card">
      <h3>Fixed asset register</h3>
      <p className="term-note">
        CSV with <span className="mono">asset_code, description, class, acquired, cost</span> and optionally{" "}
        <span className="mono">disposed, proceeds</span>. Dates as YYYY-MM-DD. Uploading again replaces it.
      </p>
      <div className="term-actions">
        <input ref={file} type="file" accept=".csv,text/csv" aria-label="Fixed asset register CSV" />
        <button className="btn btn-sm" type="button" disabled={busy} onClick={upload}>
          {busy ? "Uploading…" : register ? "Replace" : "Upload"}
        </button>
      </div>
      {error && <div className="term-error">{error}</div>}
      {register && (
        <>
          <div className="faint" style={{ fontSize: 12, marginTop: 10 }}>
            {register.filename} · {register.assets.length} assets · uploaded by {register.uploaded_by}
          </div>
          <table className="table register-table">
            <thead>
              <tr>
                <th>Class</th>
                <th className="num">Additions</th>
                <th className="num">Disposals</th>
                <th className="num">Cost held</th>
              </tr>
            </thead>
            <tbody>
              {register.classes.map((c) => (
                <tr key={c.asset_class}>
                  <td>
                    {c.asset_class}
                    <div className="faint" style={{ fontSize: 11 }}>
                      {c.assets_held} held{c.assets_added.length ? ` · ${c.assets_added.length} added` : ""}
                      {c.assets_disposed.length ? ` · ${c.assets_disposed.length} disposed` : ""}
                    </div>
                  </td>
                  <td className="num">{formatAmount(c.additions)}</td>
                  <td className="num">{formatAmount(c.disposals)}</td>
                  <td className="num">{formatAmount(c.cost_held)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <button className="btn btn-sm btn-ghost" type="button" onClick={() => setShowAssets((v) => !v)}>
            {showAssets ? "Hide assets" : "Show assets"}
          </button>
          {showAssets && (
            <table className="table register-table">
              <tbody>
                {register.assets.map((a) => (
                  <tr key={a.code}>
                    <td>
                      <span className="mono">{a.code}</span> {a.description}
                      <div className="faint" style={{ fontSize: 11 }}>
                        {a.asset_class} · acquired {formatDate(a.acquired)}
                        {a.disposed && ` · disposed ${formatDate(a.disposed)} for ${formatAmount(a.proceeds)}`}
                      </div>
                    </td>
                    <td className="num">{formatAmount(a.cost)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </div>
  );
}

function AttachmentsCard({ clientId, yearEnd, meta, attachments, usedAsEvidence, onChanged }: {
  clientId: string;
  yearEnd: string;
  meta: RecordsMeta;
  attachments: Attachment[];
  usedAsEvidence: Set<string>;
  onChanged: () => Promise<void>;
}) {
  const file = useRef<HTMLInputElement>(null);
  const [category, setCategory] = useState("withholding_certificate");
  const [accountCode, setAccountCode] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await action();
      await onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function upload() {
    const f = file.current?.files?.[0];
    if (!f) return setError("Choose a file first.");
    await run(async () => {
      await taxApi.uploadAttachment(clientId, yearEnd, f, category, accountCode, note);
      if (file.current) file.current.value = "";
      setAccountCode("");
      setNote("");
    });
  }

  return (
    <div className="card record-card">
      <h3>Supporting documents</h3>
      <p className="term-note">
        Bank statements, KRA certificates, receipts, invoices and payroll summaries for this year. PDF, PNG, JPEG, XLSX
        or CSV, up to 25 MB.
      </p>
      <label className="field">
        <span>Kind of document</span>
        <select className="input" value={category} onChange={(e) => setCategory(e.target.value)}>
          {Object.entries(meta.attachment_categories).map(([k, label]) => (
            <option key={k} value={k}>{label}</option>
          ))}
        </select>
      </label>
      <div className="record-grid" style={{ gridTemplateColumns: "1fr 2fr" }}>
        <label className="field">
          <span>Account code</span>
          <input className="input" value={accountCode} placeholder="optional" onChange={(e) => setAccountCode(e.target.value)} />
        </label>
        <label className="field">
          <span>Note</span>
          <input className="input" value={note} placeholder="optional" onChange={(e) => setNote(e.target.value)} />
        </label>
      </div>
      <div className="term-actions">
        <input ref={file} type="file" accept=".pdf,.png,.jpg,.jpeg,.xlsx,.csv" aria-label="Supporting document" />
        <button className="btn btn-sm" type="button" disabled={busy} onClick={upload}>
          {busy ? "Working…" : "Attach"}
        </button>
      </div>
      {error && <div className="term-error">{error}</div>}
      {attachments.length > 0 && (
        <ul className="attachment-list">
          {attachments.map((a) => (
            <li key={a.id}>
              <div style={{ minWidth: 0 }}>
                <button className="link attachment-name" type="button"
                  onClick={() => void taxApi.openAttachment(clientId, a.id, a.filename).catch((e) => setError((e as Error).message))}>
                  {a.filename}
                </button>
                <div className="faint" style={{ fontSize: 11 }}>
                  {meta.attachment_categories[a.category] ?? a.category}
                  {a.account_code && ` · account ${a.account_code}`} · {kb(a.size_bytes)} · {a.uploaded_by}
                  {a.note && ` · ${a.note}`}
                </div>
              </div>
              {usedAsEvidence.has(a.id) ? (
                <span className="pill pill-ok" title="Evidence for a tax credit">evidence</span>
              ) : (
                <button className="btn btn-sm btn-ghost btn-danger" type="button" disabled={busy}
                  onClick={() => void run(() => taxApi.deleteAttachment(clientId, a.id))}>
                  Remove
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
