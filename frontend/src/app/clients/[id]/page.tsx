import { ClientWorkspace } from "@/components/ClientWorkspace";

export default async function ClientPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <ClientWorkspace key={id} id={id} />;
}
