import { env } from "./env";
import { readChatEvents } from "./sse";
import type {
  BulkImportResult,
  Category,
  ChatEvent,
  CorrectedMetadata,
  FinancialEntry,
  FinanceDashboard,
  FinanceOptions,
  FinancialPage,
  Me,
  Member,
  MemberInput,
  Metrics,
  PendingReview,
  Readiness,
  UploadItem,
  UploadStatus,
} from "./types";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = "ApiError";
  }

  /** True when the backend is waking up on Render's free tier (or briefly unavailable). */
  get isUnavailable(): boolean {
    return this.status === 0 || this.status === 502 || this.status === 503 || this.status === 504;
  }
}

export type TokenGetter = () => Promise<string | null>;

interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "DELETE";
  json?: unknown;
  form?: FormData;
  signal?: AbortSignal;
  auth?: boolean;
}

async function toApiError(response: Response): Promise<ApiError> {
  let code = "http_error";
  let message = `Request failed with status ${response.status}`;
  let details: Record<string, unknown> = {};
  try {
    const body = (await response.json()) as {
      error?: { code?: string; message?: string; details?: Record<string, unknown> };
    };
    if (body.error) {
      code = body.error.code ?? code;
      message = body.error.message ?? message;
      details = body.error.details ?? {};
    }
  } catch {
    // Non-JSON error bodies (proxies, cold starts) keep the generic message.
  }
  return new ApiError(response.status, code, message, details);
}

export function createApiClient(getToken: TokenGetter, baseUrl: string = env.NEXT_PUBLIC_API_BASE_URL) {
  async function send(path: string, options: RequestOptions = {}): Promise<Response> {
    const headers = new Headers();
    if (options.auth !== false) {
      const token = await getToken();
      if (!token) throw new ApiError(401, "unauthorized", "You are signed out");
      headers.set("Authorization", `Bearer ${token}`);
    }
    let body: BodyInit | undefined;
    if (options.json !== undefined) {
      headers.set("Content-Type", "application/json");
      body = JSON.stringify(options.json);
    } else if (options.form) {
      body = options.form;
    }
    let response: Response;
    try {
      response = await fetch(`${baseUrl}${path}`, {
        method: options.method ?? "GET",
        headers,
        body,
        signal: options.signal,
      });
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") throw error;
      throw new ApiError(0, "network_error", "Cannot reach the FamGuard API");
    }
    if (!response.ok) throw await toApiError(response);
    return response;
  }

  async function json<T>(path: string, options?: RequestOptions): Promise<T> {
    const response = await send(path, options);
    return (await response.json()) as T;
  }

  return {
    me: () => json<Me>("/api/me"),
    createMember: (body: MemberInput) =>
      json<Member>("/api/members", { method: "POST", json: body }),
    updateMember: (memberId: string, body: MemberInput) =>
      json<Member>(`/api/members/${encodeURIComponent(memberId)}`, { method: "PUT", json: body }),
    deleteMember: async (memberId: string): Promise<void> => {
      await send(`/api/members/${encodeURIComponent(memberId)}`, { method: "DELETE" });
    },
    metrics: () => json<Metrics>("/api/vault/metrics"),
    readiness: async (): Promise<Readiness> => {
      try {
        return await json<Readiness>("/readyz", { auth: false });
      } catch (error) {
        if (error instanceof ApiError && error.status === 503) {
          return { status: "degraded", checks: {} };
        }
        throw error;
      }
    },
    categories: () => json<Category[]>("/api/vault/categories"),
    financialEntries: (rows: FinancialEntry[]) =>
      json<BulkImportResult>("/api/vault/financial/entries", {
        method: "POST", json: { rows },
      }),
    financialExcel: (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return json<BulkImportResult>("/api/vault/financial/excel", { method: "POST", form });
    },
    financialTemplate: async () => (await send("/api/vault/financial/template")).blob(),
    financeDashboard: () => json<FinanceDashboard>("/api/vault/financial/dashboard"),
    financeOptions: () => json<FinanceOptions>("/api/vault/financial/options"),
    financialRecords: (filters: Record<string, string>, offset = 0) => {
      const query = new URLSearchParams(Object.entries(filters).filter(([, value]) => value !== ""));
      query.set("offset", String(offset));
      return json<FinancialPage>(`/api/vault/financial/entries?${query}`);
    },
    updateFinancialEntry: async (id: string, body: FinancialEntry): Promise<void> => {
      await send(`/api/vault/financial/entries/${encodeURIComponent(id)}`, { method: "PUT", json: body });
    },
    dateFinancialEntry: async (id: string, transactionDate: string): Promise<void> => {
      await send(`/api/vault/financial/entries/${encodeURIComponent(id)}/date`, {
        method: "PUT", json: { transaction_date: transactionDate },
      });
    },
    createCategory: (slug: string, displayName: string) =>
      json<Category>("/api/vault/categories", {
        method: "POST",
        json: { category_slug: slug, category_display_name: displayName },
      }),
    bulkUpload: (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return json<BulkImportResult>("/api/vault/bulk-upload", { method: "POST", form });
    },
    upload: async (
      files: File[], familyMemberId: string, source: "upload" | "chat" = "upload",
      category?: string,
      documentDetails?: string,
    ) => {
      const form = new FormData();
      for (const file of files) form.append("files", file);
      form.append("family_member_id", familyMemberId);
      form.append("source", source);
      if (category) form.append("category", category);
      if (documentDetails?.trim()) form.append("document_details", documentDetails.trim());
      const result = await json<{ uploads: UploadItem[] }>("/api/vault/upload", {
        method: "POST",
        form,
      });
      return result.uploads;
    },
    uploadStatus: (threadId: string) =>
      json<UploadStatus>(`/api/vault/uploads/${encodeURIComponent(threadId)}`),
    recentUploads: (filters: {
      familyMemberId?: string; category?: string; limit?: number; offset?: number;
    } = {}) => {
      const query = new URLSearchParams();
      if (filters.familyMemberId) query.set("family_member_id", filters.familyMemberId);
      if (filters.category) query.set("category", filters.category);
      if (filters.limit !== undefined) query.set("limit", String(filters.limit));
      if (filters.offset !== undefined) query.set("offset", String(filters.offset));
      return json<UploadStatus[]>(`/api/vault/uploads${query.size ? `?${query}` : ""}`);
    },
    deleteDocument: async (documentId: string): Promise<void> => {
      await send(`/api/vault/documents/${encodeURIComponent(documentId)}`, { method: "DELETE" });
    },
    reviewExtraction: (documentId: string) =>
      json<UploadStatus>(`/api/vault/documents/${encodeURIComponent(documentId)}/review`, {
        method: "POST",
      }),
    pendingReviews: () => json<PendingReview[]>("/api/vault/pending-reviews"),
    resolveReview: (
      threadId: string,
      decision: "approve" | "reject",
      corrected?: CorrectedMetadata,
    ) =>
      json<{ thread_id: string; status: string }>("/api/vault/resolve-review", {
        method: "POST",
        json: { thread_id: threadId, decision, corrected_metadata: corrected ?? null },
      }),
    documentUrl: (documentId: string) =>
      json<{ url: string; expires_in: number }>(
        `/api/vault/documents/${encodeURIComponent(documentId)}/url`,
      ),
    chat: async function* (
      message: string,
      conversationId: string | null,
      signal?: AbortSignal,
    ): AsyncGenerator<ChatEvent> {
      const response = await send("/api/vault/chat", {
        method: "POST",
        json: { message, conversation_id: conversationId },
        signal,
      });
      if (!response.body) throw new ApiError(0, "stream_error", "Empty response stream");
      yield* readChatEvents(response.body, signal);
    },
  };
}

export type ApiClient = ReturnType<typeof createApiClient>;
