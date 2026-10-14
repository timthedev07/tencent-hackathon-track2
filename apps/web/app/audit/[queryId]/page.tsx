import { redirect } from 'next/navigation';

export default async function LegacyAuditRedirect({ params }: { params: Promise<{ queryId: string }> }) {
  const { queryId } = await params;
  redirect(`/compliance/${queryId}`);
}
