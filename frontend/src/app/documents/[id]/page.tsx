import { Workspace } from "@/components/Workspace";
import { FIELD_LABELS, type FieldName } from "@/lib/types";

export default async function DocumentPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ field?: string; tab?: string }>;
}) {
  const { id } = await params;
  const { field, tab } = await searchParams;
  const initialField = field && field in FIELD_LABELS ? (field as FieldName) : undefined;
  const initialTab = tab === "schedule" || tab === "review" || tab === "drafts" ? tab : "terms";
  return <Workspace key={`${id}-${initialTab}`} id={id} initialField={initialField} initialTab={initialTab} />;
}
