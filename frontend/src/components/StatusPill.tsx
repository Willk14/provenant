import type { DocumentStatus, FieldStatus } from "@/lib/types";

const FIELD: Record<FieldStatus, [string, string]> = {
  proposed: ["Proposed", ""],
  auto_accepted: ["Auto-accepted", "pill-ok"],
  needs_review: ["Needs review", "pill-warn"],
  approved: ["Approved", "pill-ok"],
  corrected: ["Corrected", "pill-ok"],
  rejected: ["Rejected", "pill-bad"],
};

const DOC: Record<DocumentStatus, [string, string]> = {
  queued: ["Queued", "pill-busy"],
  extracting: ["Extracting", "pill-busy"],
  extracted: ["Extracted", "pill-ok"],
  failed: ["Failed", "pill-bad"],
};

export function FieldStatusPill({ status }: { status: FieldStatus }) {
  const [label, cls] = FIELD[status];
  return <span className={`pill ${cls}`}>{label}</span>;
}

export function DocumentStatusPill({
  status,
  needsReview,
  missing,
}: {
  status: DocumentStatus;
  needsReview?: number | null;
  missing?: number | null;
}) {
  if (status === "extracted" && (needsReview || missing)) {
    const parts = [missing ? `${missing} missing` : "", needsReview ? `${needsReview} to review` : ""];
    return <span className="pill pill-warn">{parts.filter(Boolean).join(" · ")}</span>;
  }
  const [label, cls] = DOC[status];
  return <span className={`pill ${cls}`}>{status === "extracted" ? "Ready" : label}</span>;
}
