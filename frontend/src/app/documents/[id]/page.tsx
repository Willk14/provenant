import { Workspace } from "@/components/Workspace";
import { FIELD_ORDER, type FieldName } from "@/lib/types";

export default async function DocumentPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ field?: string }>;
}) {
  const { id } = await params;
  const { field } = await searchParams;
  const initialField = FIELD_ORDER.includes(field as FieldName) ? (field as FieldName) : undefined;
  return <Workspace key={id} id={id} initialField={initialField} />;
}
