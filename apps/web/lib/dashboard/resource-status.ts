export type DashboardResourceStatus = "loading" | "ready" | "stale" | "error";

export type DashboardResourceSnapshot = {
  status: DashboardResourceStatus;
  complete: boolean;
};

export function hasUsableDashboardData(snapshot: DashboardResourceSnapshot) {
  return snapshot.status === "ready" || snapshot.status === "stale";
}
