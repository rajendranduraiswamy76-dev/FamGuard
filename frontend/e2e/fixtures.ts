import type { Page, Route } from "@playwright/test";

export const API = "http://api.test";

const member = (id: string, name: string, role: string, email: string | null) => ({
  id, display_name: name, email, role, relationship: null as string | null,
  has_login: email !== null, created_at: "2026-01-01T00:00:00Z",
});

export const me = {
  member: member("m-ann", "Ann", "admin", "ann@example.com"),
  family: { id: "f-1", name: "Smith Family", timezone: "UTC", currency: "USD" },
  members: [member("m-ann", "Ann", "admin", "ann@example.com"), member("m-john", "John", "dependent", null)],
};

export const metrics = {
  documents_total: 12,
  vectors_total: 31,
  by_category: [{ category: "identitycards", documents: 3, vectors: 5 }],
  upcoming_expirations: [
    { document_id: "d-1", document_name: "John Passport", member_name: "John", category: "identitycards", expiration_date: "2026-10-21", days_remaining: 3 },
  ],
  year_expirations: [
    { document_id: "d-year", document_name: "Annual Insurance", member_name: "Ann", category: "medical", expiration_date: "2027-06-01", days_remaining: 120 },
  ],
  open_reviews: 1,
  processing: 0,
  failed: 0,
};

export const categories = [
  { id: "c-1", category_slug: "identitycards", category_display_name: "Identity Cards", is_baseline: true },
  { id: "c-2", category_slug: "miscellaneous", category_display_name: "Miscellaneous", is_baseline: true },
];

export const reviews = [
  {
    id: "r-1", thread_id: "ing-1", family_member_id: "m-john", document_id: "d-9",
    original_filename: "john-id.jpg", raw_text_preview: "ID CARD JOHN",
    suggested_metadata: {
      category: "identitycards", document_name: "John ID", expiration_date: null,
      requires_alert: true, extracted_text: "ID CARD JOHN", financial_value: null, confidence_score: 0.6,
    },
    confidence_score: 0.6, review_reasons: ["expiration_unparsed"], created_at: "2026-10-17T10:00:00Z",
  },
];

export const chatSse = [
  'event: filter\ndata: {"member_id":"m-john","member":"John","category":"identitycards","label":"Context Filtered: John | identitycards"}\n\n',
  'event: token\ndata: {"text":"John\'s passport expires 2031-05-04."}\n\n',
  'event: sources\ndata: [{"document_id":"d-1","document_name":"John Passport","category":"identitycards","score":0.92}]\n\n',
  'event: done\ndata: {"conversation_id":"c-1"}\n\n',
].join("");

const cors = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "Authorization, Content-Type",
  "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
};

export function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: "application/json", headers: cors, body: JSON.stringify(body) });
}

export interface ApiMockOptions {
  onResolve?: (body: Record<string, unknown>) => void;
  uploadStatuses?: string[];
}

/** Routes every backend call the UI makes to deterministic fixtures. */
export async function mockApi(page: Page, options: ApiMockOptions = {}) {
  let pendingReviews = [...reviews];
  const statuses = [...(options.uploadStatuses ?? ["processing", "review_required"])];

  await page.route(`${API}/**`, async (route) => {
    const request = route.request();
    if (request.method() === "OPTIONS") return route.fulfill({ status: 204, headers: cors });
    const url = new URL(request.url());
    const key = `${request.method()} ${url.pathname}`;

    if (key === "GET /api/me") return json(route, me);
    if (key === "GET /api/vault/metrics") return json(route, metrics);
    if (key === "GET /api/vault/categories") return json(route, categories);
    if (key === "GET /api/vault/uploads") return json(route, []);
    if (key === "GET /api/vault/financial/options") return json(route, {
      subcategories: { income: ["Salary", "Bonus"], expense: ["Groceries", "Rent"] },
      currencies: ["SGD", "USD", "INR", "NOK"],
    });
    if (key === "GET /api/vault/financial/dashboard") return json(route, {
      today: "2026-11-09", months: [], expected_cashflows: [], upcoming_bills: [], undated: [], undated_count: 0,
    });
    if (key === "GET /api/vault/financial/template") return route.fulfill({
      contentType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      headers: cors, body: Buffer.from("PK synthetic workbook response"),
    });
    if (key === "POST /api/vault/financial/entries") {
      const body = request.postDataJSON() as { rows: unknown[] };
      return json(route, { accepted: body.rows.length, rejected: 0, errors: [], errors_truncated: false });
    }
    if (key === "POST /api/vault/financial/excel") {
      return json(route, { accepted: 2, rejected: 0, errors: [], errors_truncated: false });
    }
    if (key === "GET /readyz") {
      return json(route, { status: "ok", checks: { postgres: "ok", redis: "ok", pinecone: "ok" } });
    }
    if (key === "GET /api/vault/pending-reviews") return json(route, pendingReviews);
    if (key === "POST /api/vault/resolve-review") {
      const body = request.postDataJSON() as Record<string, unknown>;
      options.onResolve?.(body);
      pendingReviews = pendingReviews.filter((r) => r.thread_id !== body.thread_id);
      return json(route, { thread_id: body.thread_id, status: "approved" }, 202);
    }
    if (key === "POST /api/vault/chat") {
      return route.fulfill({
        status: 200,
        headers: { ...cors, "Content-Type": "text/event-stream" },
        body: chatSse,
      });
    }
    if (key === "POST /api/vault/upload") {
      return json(route, { uploads: [{ thread_id: "ing-7", document_id: "d-7", filename: "id.jpg" }] }, 202);
    }
    if (key === "GET /api/vault/uploads/ing-7") {
      const status = statuses.length > 1 ? statuses.shift() : statuses[0];
      return json(route, {
        thread_id: "ing-7", document_id: "d-7", family_member_id: "m-ann", status,
        filename: "id.jpg", category: "miscellaneous", document_name: "id.jpg",
        expiration_date: null, requires_alert: false, pending_review_id: null, error: null,
      });
    }
    return json(route, { error: { code: "not_found", message: `Unmocked ${key}`, details: {} } }, 404);
  });
}
