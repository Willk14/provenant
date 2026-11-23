import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LegalReviewPanel } from "@/components/LegalReviewPanel";
import { api } from "@/lib/api";
import type { DocumentSummary, LegalFinding, LegalPlaybook, LegalReview } from "@/lib/types";

vi.mock("@/lib/api", () => ({
  api: {
    legalReviews: vi.fn(),
    legalReview: vi.fn(),
    legalPlaybook: vi.fn(),
    startLegalReview: vi.fn(),
    decideLegalFinding: vi.fn(),
    askLegalQuestion: vi.fn(),
    saveLegalPlaybook: vi.fn(),
  },
}));

const doc = { id: "doc-1", kind: "credit_agreement", filename: "facility.pdf" } as DocumentSummary;
const playbook: LegalPlaybook = {
  version: "playbook-v1", sha256: "a", updated_by: null,
  topics: [{ key: "change_of_control", label: "Change of control", position: "Notice only, with no consent.", standard: true },
           { key: "indemnity", label: "Indemnity", position: "Mutual and limited to third parties.", standard: true }],
};
const finding: LegalFinding = {
  id: "f1", topic: "change_of_control", severity: "high", title: "Cancellation right",
  explanation: "The lender may cancel.", suggestion: "Ask for notice only.",
  citation: { document_id: "doc-1", filename: "facility.pdf", quote: "the Lender may cancel", page: 2, bbox: [1, 2, 3, 4] },
  proposed_term: null, proposed_value: null, decision: null, decision_note: null, decided_by: null, field_id: null,
};
const review: LegalReview = {
  id: "r1", status: "done", agent: "claude-managed:agent", playbook_version: "playbook-v1", playbook_sha256: "a",
  summary: "One point to decide.", not_found: ["indemnity"], error: null, requested_by: "pat", created_at: "2026-11-23T10:00:00Z",
  finished_at: "2026-11-23T10:05:00Z", findings: [finding], questions: [],
};

describe("LegalReviewPanel", () => {
  beforeEach(() => {
    vi.mocked(api.legalPlaybook).mockResolvedValue(playbook);
    vi.mocked(api.legalReviews).mockResolvedValue([review]);
    vi.mocked(api.legalReview).mockResolvedValue(review);
    vi.mocked(api.decideLegalFinding).mockResolvedValue(finding);
  });
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("shows the quote, opens its place on the PDF and lists topics not found", async () => {
    const onCite = vi.fn();
    render(<LegalReviewPanel doc={doc} onCite={onCite} />);
    expect(await screen.findByText("the Lender may cancel")).toBeTruthy();
    expect(screen.getByText(/Indemnity\. A lawyer should confirm/)).toBeTruthy();
    fireEvent.click(screen.getByTitle("Show on the PDF"));
    expect(onCite).toHaveBeenCalledWith("f1", finding.citation);
  });

  it("needs a reason before a finding can be rejected", async () => {
    render(<LegalReviewPanel doc={doc} onCite={vi.fn()} />);
    const reject = (await screen.findByRole("button", { name: "Reject" })) as HTMLButtonElement;
    expect(reject.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Note on Cancellation right"), { target: { value: "Not relevant here" } });
    expect(reject.disabled).toBe(false);
    fireEvent.click(reject);
    await waitFor(() => expect(api.decideLegalFinding).toHaveBeenCalledWith("doc-1", "r1", "f1", "rejected", "Not relevant here"));
  });

  it("says plainly when the review used only the keyword scan", async () => {
    vi.mocked(api.legalReview).mockResolvedValue({ ...review, agent: "baseline-keywords-v1" });
    render(<LegalReviewPanel doc={doc} onCite={vi.fn()} />);
    expect(await screen.findByText("Keyword scan")).toBeTruthy();
  });
});
