import { Workspace } from "@/components/Workspace";
import { FIELD_LABELS, type FieldName } from "@/lib/types";

export default async function DocumentPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ field?: string; tab?: string; page?: string; bbox?: string }>;
}) {
  const { id } = await params;
  const { field, tab, page, bbox } = await searchParams;
  const box = bbox?.split(",").map(Number) ?? [];
  const pageNumber = Number(page);
  const initialCitation = Number.isInteger(pageNumber) && pageNumber >= 1 && box.length === 4 && box.every(Number.isFinite)
    ? { page: pageNumber, bbox: box as [number, number, number, number] }
    : undefined;
  const initialField = field && field in FIELD_LABELS ? (field as FieldName) : undefined;
  const initialTab = tab === "schedule" || tab === "review" || tab === "drafts" || tab === "ask" ? tab : "terms";
  return (
    <Workspace key={`${id}-${initialTab}-${page ?? ""}-${bbox ?? ""}`} id={id} initialField={initialField}
      initialTab={initialTab} initialCitation={initialCitation} />
  );
}
