"use client";

import { useCallback, useEffect, useState } from "react";

import { formatAmount } from "@/lib/format";
import { TREATMENT_LABELS, taxApi, type Account, type Mapping } from "@/lib/tax";

const CLAUDE_RULE_PREFIX = "claude: ";
const STATUS_PILL = { unmapped: "pill-bad", suggested: "pill-warn", confirmed: "pill-ok" } as const;
const STATUS_LABEL = { unmapped: "Unmapped", suggested: "Suggested", confirmed: "Confirmed" } as const;
const FIRM_RULE_PREFIX = "firm: "; // matches provenant.tax.mapping.FIRM_RULE_PREFIX

interface Props {
  clientId: string;
  yearEnd: string;
  onChanged: () => void;
}

export function MappingTab({ clientId, yearEnd, onChanged }: Props) {
  const [mapping, setMapping] = useState<Mapping | null>(null);
  const [filter, setFilter] = useState<"attention" | "all">("attention");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [claudeNote, setClaudeNote] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setMapping(await taxApi.mapping(clientId, yearEnd));
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [clientId, yearEnd]);

  useEffect(() => {
    void load();
  }, [load]);

  if (!mapping) return error ? <div className="error-banner">{error}</div> : <div className="empty">Loading…</div>;

  const counts = { unmapped: 0, suggested: 0, confirmed: 0 };
  mapping.accounts.forEach((a) => counts[a.status]++);
  const shown = filter === "all" ? mapping.accounts : mapping.accounts.filter((a) => a.status !== "confirmed");

  async function mapWithClaude() {
    setBusy(true);
    setError(null);
    setClaudeNote(null);
    try {
      const got = await taxApi.mapWithClaude(clientId, yearEnd);
      setClaudeNote(got.sent === 0 ? "Every account is already mapped by the firm's practice or confirmed by a person."
        : `Claude suggested ${got.suggested} of ${got.sent} account${got.sent === 1 ? "" : "s"}`
          + (got.left ? `; ${got.left} left for you to map.` : ".") + " Check each one, then confirm.");
      await load();
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function confirmAll() {
    setBusy(true);
    try {
      await taxApi.confirmSuggestions(clientId, yearEnd);
      await load();
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mapping-layout">
      <section>
        <div className="mapping-bar">
          <span className="pill pill-bad">{counts.unmapped} unmapped</span>
          <span className="pill pill-warn">{counts.suggested} suggested</span>
          <span className="pill pill-ok">{counts.confirmed} confirmed</span>
          <div className="segmented" role="group" aria-label="Show" style={{ marginLeft: "auto" }}>
            <button type="button" aria-pressed={filter === "attention"} onClick={() => setFilter("attention")}>
              Needs attention
            </button>
            <button type="button" aria-pressed={filter === "all"} onClick={() => setFilter("all")}>
              All accounts
            </button>
          </div>
          <button className="btn btn-sm" disabled={busy || counts.unmapped + counts.suggested === 0} onClick={mapWithClaude}>
            {busy ? "Working…" : "Map with Claude"}
          </button>
          <button className="btn btn-primary btn-sm" disabled={busy || counts.suggested === 0} onClick={confirmAll}>
            Confirm {counts.suggested} suggestions
          </button>
        </div>
        {error && <div className="error-banner">{error}</div>}
        {claudeNote && <div className="callout callout-ok">{claudeNote}</div>}
        <div className="card table-scroll">
          {shown.length === 0 ? (
            <div className="empty">Every account is mapped and confirmed. The tax computation can run.</div>
          ) : (
            <table className="table map-table">
              <thead>
                <tr>
                  <th>Account</th>
                  <th style={{ textAlign: "right" }}>Balance (Dr / −Cr)</th>
                  <th>Financial statement line</th>
                  <th>Tax treatment</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {shown.map((a) => (
                  <AccountRow
                    key={a.code}
                    account={a}
                    mapping={mapping}
                    onSave={async (fsLine, treatment) => {
                      await taxApi.mapAccount(clientId, a.code, fsLine, treatment);
                      await load();
                      onChanged();
                    }}
                  />
                ))}
              </tbody>
            </table>
          )}
        </div>
        <p className="term-note">
          Suggestions come first from how the firm confirmed the same account name at other clients (when they
          clearly agree), then from fixed keyword rules on account names. None is used until a person confirms
          it, and every confirmation is kept in the mapping history with who made it.
        </p>
      </section>

      <aside className="card lead">
        <div className="term-label" style={{ padding: "12px 14px 0" }}>
          Lead schedule · {mapping.trial_balance.filename}
        </div>
        {(["profit_or_loss", "position"] as const).map((statement) => (
          <table key={statement} className="workings-table lead-table">
            <tbody>
              <tr>
                <td colSpan={2} className="lead-head">
                  {statement === "profit_or_loss" ? "Profit or loss" : "Financial position"}
                </td>
              </tr>
              {mapping.lead_schedule
                .filter((l) => l.statement === statement)
                .map((l) => (
                  <tr key={l.fs_line} title={`Accounts: ${l.codes.join(", ")}`}>
                    <td>{l.label}</td>
                    <td className="num">{formatAmount(l.amount)}</td>
                  </tr>
                ))}
            </tbody>
          </table>
        ))}
      </aside>
    </div>
  );
}

function AccountRow({
  account,
  mapping,
  onSave,
}: {
  account: Account;
  mapping: Mapping;
  onSave: (fsLine: string, treatment: string) => Promise<void>;
}) {
  const [fsLine, setFsLine] = useState(account.fs_line ?? "");
  const [treatment, setTreatment] = useState(account.tax_treatment ?? "");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const statement = fsLine ? mapping.fs_lines[fsLine]?.statement : null;
  const treatments = mapping.tax_treatments.filter((t) =>
    statement === "position" ? t === "balance_sheet" : t !== "balance_sheet",
  );
  const changed = fsLine !== (account.fs_line ?? "") || treatment !== (account.tax_treatment ?? "");
  const fromFirm = account.rule?.startsWith(FIRM_RULE_PREFIX) ?? false;
  const fromClaude = account.rule?.startsWith(CLAUDE_RULE_PREFIX) ?? false;

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await onSave(fsLine, treatment);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <tr>
      <td>
        <div style={{ fontWeight: 550 }}>
          <span className="mono faint">{account.code}</span> {account.name}
        </div>
        <span className={`pill ${STATUS_PILL[account.status]}`} title={account.rule && !fromFirm && !fromClaude ? `Suggested by keyword rule: ${account.rule}` : undefined}>
          {STATUS_LABEL[account.status]}
          {account.confirmed_by ? ` · ${account.confirmed_by}` : ""}
        </span>
        {fromClaude && account.status === "suggested" && (
          <div className="faint" style={{ fontSize: 12, marginTop: 3 }}>
            <span className="pill pill-manual">Claude</span> {account.rule?.slice(CLAUDE_RULE_PREFIX.length)}
          </div>
        )}
        {fromFirm && account.status === "suggested" && (
          <div className="faint" style={{ fontSize: 12, marginTop: 3 }}>
            Firm practice: {account.rule?.slice(FIRM_RULE_PREFIX.length)}
          </div>
        )}
        {error && <div className="term-error">{error}</div>}
      </td>
      <td className="num" style={{ textAlign: "right" }}>
        {formatAmount(account.balance)}
      </td>
      <td>
        <select
          className="input"
          value={fsLine}
          aria-label={`Financial statement line for ${account.code}`}
          onChange={(e) => {
            setFsLine(e.target.value);
            setTreatment(mapping.fs_lines[e.target.value]?.default ?? "");
          }}
        >
          <option value="">Choose…</option>
          {(["profit_or_loss", "position"] as const).map((st) => (
            <optgroup key={st} label={st === "profit_or_loss" ? "Profit or loss" : "Financial position"}>
              {Object.entries(mapping.fs_lines)
                .filter(([, l]) => l.statement === st)
                .map(([key, l]) => (
                  <option key={key} value={key}>
                    {l.label}
                  </option>
                ))}
            </optgroup>
          ))}
        </select>
      </td>
      <td>
        <select
          className="input"
          value={treatment}
          disabled={!fsLine}
          aria-label={`Tax treatment for ${account.code}`}
          onChange={(e) => setTreatment(e.target.value)}
        >
          <option value="">Choose…</option>
          {treatments.map((t) => (
            <option key={t} value={t}>
              {TREATMENT_LABELS[t] ?? t}
            </option>
          ))}
        </select>
      </td>
      <td style={{ textAlign: "right" }}>
        <button
          className={`btn btn-sm ${account.status !== "confirmed" || changed ? "btn-primary" : ""}`}
          disabled={busy || !fsLine || !treatment || (account.status === "confirmed" && !changed)}
          onClick={save}
        >
          {account.status === "confirmed" && !changed ? "Confirmed" : changed ? "Save" : "Confirm"}
        </button>
      </td>
    </tr>
  );
}
