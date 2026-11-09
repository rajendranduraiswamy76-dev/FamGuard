import type { components } from "./openapi-schema";

export type Role = components["schemas"]["MemberOut"]["role"];
export type DocumentStatus = components["schemas"]["UploadStatusOut"]["status"];

export type Member = components["schemas"]["MemberOut"];
export type MemberInput = components["schemas"]["MemberCreate"];

export type Family = components["schemas"]["FamilyOut"];

export type Me = components["schemas"]["MeOut"];

export type Category = components["schemas"]["CategoryOut"];

export type CategoryMetric = components["schemas"]["CategoryMetric"];

export type UpcomingExpiration = components["schemas"]["UpcomingExpiration"];

export type Metrics = components["schemas"]["MetricsOut"];

export type ReadinessState = "ok" | "error";

export interface Readiness {
  status: "ok" | "degraded";
  checks: Record<string, ReadinessState>;
}

export type UploadItem = components["schemas"]["UploadItem"];

export type UploadStatus = components["schemas"]["UploadStatusOut"];

export type BulkRowError = components["schemas"]["BulkRowError"];

export type BulkImportResult = components["schemas"]["BulkImportOut"];

export interface SuggestedMetadata {
  category?: string;
  document_name?: string;
  expiration_date?: string | null;
  requires_alert?: boolean;
  extracted_text?: string;
  financial_value?: number | null;
  confidence_score?: number;
}

export type PendingReview = Omit<
  components["schemas"]["PendingReviewOut"],
  "suggested_metadata"
> & { suggested_metadata: SuggestedMetadata };

export type CorrectedMetadata = components["schemas"]["CorrectedMetadata"];

export type FinancialEntry = components["schemas"]["FinancialRow"];
export type FinanceDashboard = components["schemas"]["FinanceDashboard"];
export type FinanceOptions = components["schemas"]["FinanceOptions"];
export type FinanceRecord = components["schemas"]["FinanceRecord"];
export type FinancialPage = components["schemas"]["FinancialPage"];

export interface ChatFilter {
  member_id: string | null;
  member: string | null;
  category: string | null;
  label?: string;
}

export interface ChatSource {
  document_id: string;
  document_name: string;
  category: string;
  score: number;
}

export type ChatEvent =
  | { event: "filter"; data: ChatFilter }
  | { event: "token"; data: { text: string } }
  | { event: "sources"; data: ChatSource[] }
  | { event: "error"; data: { message: string } }
  | { event: "done"; data: { conversation_id: string | null } };
