import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { KINDS, UploadBox } from "@/components/UploadBox";

vi.mock("next/link", () => ({ default: ({ href, children }: { href: string; children: React.ReactNode }) => <a href={href}>{children}</a> }));
vi.mock("@/lib/api", () => ({ api: { upload: vi.fn(), uploadTrialBalance: vi.fn() } }));
vi.mock("@/lib/tax", () => ({
  taxApi: {
    clients: vi.fn(async () => [{ id: "k1", name: "Kilima Analytics Ltd", year_ends: ["2026-12-31"], created_at: "2026-11-22" }]),
    meta: vi.fn(async () => ({ attachment_categories: { receipt: "Receipt" } })),
  },
  checksApi: {}, etimsApi: {}, ledgerApi: {}, statementsApi: {},
}));

const contractKinds = Object.values(KINDS).filter((k) => k.group === "Contracts").length;
const recordKinds = Object.values(KINDS).filter((k) => k.group === "Client records").length;

describe("UploadBox", () => {
  beforeEach(() => render(<UploadBox agreements={[]} onContractsChanged={async () => undefined} />));
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("shows every kind of document as a visible choice, grouped in tabs", async () => {
    expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual([
      `Contracts ${contractKinds}`, `Client records ${recordKinds}`, "Evidence 1",
    ]);
    const contracts = screen.getByRole("group", { name: "Contracts you can upload" });
    expect(within(contracts).getAllByRole("button")).toHaveLength(contractKinds);
    expect(screen.getByRole("button", { name: "Lease" }).getAttribute("aria-pressed")).toBe("true");
  });

  it("switches group and kind, and shows what that kind needs", async () => {
    fireEvent.click(screen.getByRole("tab", { name: /Client records/ }));
    const records = screen.getByRole("group", { name: "Client records you can upload" });
    expect(within(records).getAllByRole("button")).toHaveLength(recordKinds);
    expect(screen.getByText("Drop a year-end trial balance here")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "M-Pesa statement" }));
    expect(screen.getByRole("button", { name: "M-Pesa statement" }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByText("Account name")).toBeTruthy();
    expect(screen.getByText(KINDS.mpesa.hint)).toBeTruthy();
  });
});
