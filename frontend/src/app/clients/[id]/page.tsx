import { ClientWorkspace } from "@/components/ClientWorkspace";

export default async function ClientPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ tab?: string; year?: string }>;
}) {
  const { id } = await params;
  const { tab, year } = await searchParams;
  return <ClientWorkspace key={id} id={id} initialTab={tab} initialYear={year} />;
}
