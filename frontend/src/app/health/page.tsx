import { HealthIndicators } from "@/app/components/HealthIndicators";
import { VaultStatistics } from "@/app/components/VaultStatistics";

export default function HealthPage() {
  return (
    <div className="space-y-6">
      <div className="page-heading">
        <div>
        <h1>Service health</h1>
        <p>
          Database, processing queue, and vector index status. Refreshes every 30 seconds.
        </p>
        </div>
      </div>
      <div className="grid items-start gap-6 lg:grid-cols-2">
        <VaultStatistics />
        <div><HealthIndicators /></div>
      </div>
    </div>
  );
}
