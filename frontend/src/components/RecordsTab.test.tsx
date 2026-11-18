import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { apiMock, taxApiMock } = vi.hoisted(() => ({ apiMock: { documents: vi.fn() }, taxApiMock: {
  meta: vi.fn(), inputs: vi.fn(), register: vi.fn(), attachments: vi.fn(), saveInputs: vi.fn(),
  rollForward: vi.fn(), uploadRegister: vi.fn(), uploadAttachment: vi.fn(), deleteAttachment: vi.fn(),
  openAttachment: vi.fn(),
} }));

vi.mock("@/lib/api", () => ({
  api: apiMock,
  ApiError: class ApiError extends Error { status = 0; },
}));
vi.mock("@/lib/tax", () => ({ taxApi: taxApiMock }));

import { RecordsTab } from "./RecordsTab";

const emptyInputs = {
  losses_brought_forward: "0",
  allowances: [],
  credits: [],
  prior_return: { reference: "", filed_on: "", assessed_tax: "", agriculture_income_ratio: "0" },
  updated_by: null,
};
const metadata = {
  credit_kinds: { instalment: "Instalment tax", withholding: "Withholding tax", overpayment: "Overpayment", other: "Other" },
  attachment_categories: { withholding_certificate: "Withholding tax certificate", instalment_receipt: "Instalment tax receipt" },
};

function renderRecords() {
  const onSaved = vi.fn();
  render(<RecordsTab clientId="client-1" yearEnd="2026-12-31" onSaved={onSaved} onOpenComputation={vi.fn()} />);
  return onSaved;
}

describe("RecordsTab", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    taxApiMock.meta.mockResolvedValue(metadata);
    taxApiMock.inputs.mockResolvedValue(emptyInputs);
    taxApiMock.register.mockResolvedValue(null);
    taxApiMock.attachments.mockResolvedValue([]);
    taxApiMock.saveInputs.mockImplementation(async (_client: string, _year: string, input: typeof emptyInputs) => input);
  });
  afterEach(() => cleanup());

  it("saves edited preparer inputs and notifies the parent", async () => {
    const onSaved = renderRecords();
    const losses = await screen.findByLabelText("Tax losses brought forward");
    fireEvent.change(losses, { target: { value: "1250.50" } });
    fireEvent.click(screen.getByRole("button", { name: "Save inputs" }));

    await waitFor(() => expect(taxApiMock.saveInputs).toHaveBeenCalledWith("client-1", "2026-12-31",
      expect.objectContaining({ losses_brought_forward: "1250.50" })));
    expect(await screen.findByText("Saved. The tax computation uses these figures now.")).toBeTruthy();
    expect(onSaved).toHaveBeenCalledOnce();
  });

  it("locks additions and disposals when the register supplies the class", async () => {
    taxApiMock.inputs.mockResolvedValue({ ...emptyInputs, allowances: [{ name: "Plant and machinery",
      wdv_brought_forward: "5000", additions: "0", disposals: "0", rate_percent: "25",
      method: "reducing", basis: "Second Schedule" }] });
    taxApiMock.register.mockResolvedValue({ id: "register-1", filename: "assets.csv", sha256: "a".repeat(64),
      uploaded_by: "Pat", classes: [{ asset_class: "Plant and machinery", additions: "10000", disposals: "2500",
        cost_held: "", assets_added: [], assets_disposed: [], assets_held: 2 }], assets: [] });
    renderRecords();

    const additions = await screen.findByLabelText("Additions · register") as HTMLInputElement;
    const disposals = screen.getByLabelText("Disposal proceeds · register") as HTMLInputElement;
    expect(additions.disabled).toBe(true);
    expect(disposals.disabled).toBe(true);
    expect(additions.value).toBe("10,000.00");
    expect(disposals.value).toBe("2,500.00");
  });

  it("saves the explicit business-cessation confirmation with the preparer inputs", async () => {
    renderRecords();
    fireEvent.click(await screen.findByRole("button", { name: "Add class" }));
    const ceased = screen.getByRole("checkbox", { name: /Business ceased this year for this class/ });
    fireEvent.click(ceased);
    fireEvent.click(screen.getByRole("button", { name: "Save inputs" }));

    await waitFor(() => expect(taxApiMock.saveInputs).toHaveBeenCalledWith("client-1", "2026-12-31",
      expect.objectContaining({ allowances: [expect.objectContaining({ business_ceased: true })] })));
  });

  it("marks an attachment as evidence when a credit refers to it", async () => {
    taxApiMock.inputs.mockResolvedValue({ ...emptyInputs, credits: [{ kind: "withholding", description: "Safari Bank",
      amount: "2500", reference: "WHT-1", evidence: "attachment-1" }] });
    taxApiMock.attachments.mockResolvedValue([{ id: "attachment-1", year_end: "2026-12-31",
      category: "withholding_certificate", filename: "wht.pdf", content_type: "application/pdf", size_bytes: 2048,
      account_code: null, note: "", uploaded_by: "Pat", created_at: "2026-12-31T00:00:00Z" }]);
    renderRecords();

    expect(await screen.findByTitle("Evidence for a tax credit")).toBeTruthy();
    expect(screen.getByText("evidence")).toBeTruthy();
  });
});
