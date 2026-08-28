import { fetchSnapshot } from "../../../lib/snapshot";

export const dynamic = "force-dynamic";

const NO_STORE = { "Cache-Control": "no-store" };

export async function GET() {
  try {
    const snapshot = await fetchSnapshot();
    return Response.json(snapshot, { headers: NO_STORE });
  } catch {
    return Response.json(
      { error: "dashboard_snapshot_unavailable" },
      { status: 503, headers: NO_STORE },
    );
  }
}
