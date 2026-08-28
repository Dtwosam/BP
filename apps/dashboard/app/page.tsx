import { DashboardClient } from "./dashboard-client";
import { fetchSnapshot } from "../lib/snapshot";
import type { DashboardSnapshot } from "../lib/snapshot";

export const dynamic = "force-dynamic";

export default async function HomePage() {
  let initialSnapshot: DashboardSnapshot | null = null;
  try {
    initialSnapshot = await fetchSnapshot();
  } catch {
    initialSnapshot = null;
  }
  return <DashboardClient initialSnapshot={initialSnapshot} />;
}
