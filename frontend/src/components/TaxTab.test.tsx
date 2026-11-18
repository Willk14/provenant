import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { apiMock, taxApiMock } = vi.hoisted(() => ({ apiMock: { documents: vi.fn() }, taxApiMock: {
  computation: vi.fn(), approveComputation: vi.fn(), exportComputation: vi.fn(), linkLease: vi.fn(), openAttachment: vi.fn(),
} }));

vi.mock("@/lib/api", () => ({
  api: apiMock,
  ApiError: class ApiError extends Error { status = 0; },
}));
vi.mock("@/lib/tax", () => ({ taxApi: taxApiMock }));

import { TaxTab } from "./TaxTab";
import type { Computation } from "@/lib/tax";

function taxComputation(taxDue: string, taxPayable: string,
  instalmentPlan: Computation["instalment_plan"] = null): Computation {
  return {
    approval: null,
    changed_since_approval: false,
    rules_version: "ke-2026.3",
    profit_before_tax: "1000.00",
    adjustments: [],
    taxable_before_losses: "1000.00",
    losses_brought_forward: "0.00",
    losses_used: "0.00",
    losses_carried_forward: "0.00",
    taxable_profit: "1000.00",
    rate: "0.30",
    tax_payable: taxPayable,
    deferred: [],
    deferred_tax: "0.00",
    credits: [{ label: "Withholding tax", amount: "-350.00", basis: "Tax credit", sources: ["credit:0"] }],
    tax_due: taxDue,
    instalment_plan: instalmentPlan,
    credit_items: [{ kind: "withholding", description: "Safari Bank", amount: "350.00", reference: "WHT-1",
      evidence: null, evidence_filename: null }],
    warnings: [],
    leases: [],
    allowances: [],
    accounts: {},
  };
}

describe("TaxTab", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMock.documents.mockResolvedValue([]);
  });
  afterEach(() => cleanup());

  it("applies credits to tax payable and shows the remaining tax due", async () => {
    taxApiMock.computation.mockResolvedValue(taxComputation("150.00", "500.00"));
    render(<TaxTab clientId="client-1" yearEnd="2026-12-31" onOpenMapping={vi.fn()} onOpenRecords={vi.fn()} />);

    expect(await screen.findByText("Tax due after credits")).toBeTruthy();
    expect(screen.getByText(/Safari Bank.*WHT-1.*350\.00/)).toBeTruthy();
    expect(screen.getByText("Tax due")).toBeTruthy();
    expect(screen.getAllByText("150.00").length).toBeGreaterThan(0);
  });

  it("labels and displays a refundable balance after credits", async () => {
    taxApiMock.computation.mockResolvedValue(taxComputation("-50.00", "300.00"));
    render(<TaxTab clientId="client-1" yearEnd="2026-12-31" onOpenMapping={vi.fn()} onOpenRecords={vi.fn()} />);

    expect((await screen.findAllByText("Tax refundable")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("50.00").length).toBeGreaterThan(0);
  });

  it("shows instalment dates, source basis and outstanding amounts", async () => {
    const computation = taxComputation("150.00", "500.00", {
      annual_amount: "400.00", basis_used: "current-year estimate", prior_year_basis: null,
      current_year_basis: "400.00", withholding_reduction: "100.00", agriculture_income_ratio: "0",
      balance_due_date: "2027-04-30", balance_due_basis: "Income Tax Act s.52B",
      balance_due_amount: "150.00", unallocated_instalment_payments: "0",
      installments: [{ number: 1, due_date: "2026-04-20", proportion: "0.25", amount: "100.00",
        paid_to_date: "25.00", outstanding: "75.00", basis: "Income Tax Act s.12 and Twelfth Schedule",
        sources: ["current_year_tax_computation", "withholding_credits"] }],
    });
    taxApiMock.computation.mockResolvedValue(computation);
    render(<TaxTab clientId="client-1" yearEnd="2026-12-31" onOpenMapping={vi.fn()} onOpenRecords={vi.fn()} />);

    expect(await screen.findByText("Instalment tax plan")).toBeTruthy();
    expect(screen.getByText(/Income Tax Act s\.12 and Twelfth Schedule/)).toBeTruthy();
    expect(screen.getByText("75.00")).toBeTruthy();
    expect(screen.getByText(/Current computation balance: 150\.00/)).toBeTruthy();
  });
});
