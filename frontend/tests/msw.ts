import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { vi } from "vitest";

import type { Category, Me, Metrics, PendingReview } from "@/lib/types";

export const API = "http://api.test";

export const me: Me = {
  member: {
    id: "m-ann", display_name: "Ann", email: "ann@example.com", role: "admin",
    has_login: true, created_at: "2026-01-01T00:00:00Z",
  },
  family: { id: "f-1", name: "Smith Family", timezone: "UTC", currency: "USD" },
  members: [
    { id: "m-ann", display_name: "Ann", email: "ann@example.com", role: "admin", has_login: true, created_at: "2026-01-01T00:00:00Z" },
    { id: "m-john", display_name: "John", email: null, role: "dependent", has_login: false, created_at: "2026-01-02T00:00:00Z" },
  ],
};

export const metrics: Metrics = {
  documents_total: 12,
  vectors_total: 31,
  by_category: [
    { category: "identitycards", documents: 3, vectors: 5 },
    { category: "medical", documents: 9, vectors: 26 },
  ],
  upcoming_expirations: [
    { document_id: "d-1", document_name: "John Passport", member_name: "John", category: "identitycards", expiration_date: "2026-10-21", days_remaining: 3 },
    { document_id: "d-2", document_name: "Ann Insurance", member_name: "Ann", category: "medical", expiration_date: "2026-11-02", days_remaining: 15 },
  ],
  open_reviews: 2,
  processing: 1,
  failed: 0,
};

export const categories: Category[] = [
  { id: "c-1", category_slug: "identitycards", category_display_name: "Identity Cards", is_baseline: true },
  { id: "c-2", category_slug: "medical", category_display_name: "Medical", is_baseline: true },
  { id: "c-3", category_slug: "miscellaneous", category_display_name: "Miscellaneous", is_baseline: true },
];

export const reviews: PendingReview[] = [
  {
    id: "r-1", thread_id: "ing-1", family_member_id: "m-john", document_id: "d-9",
    original_filename: "john-id.jpg", raw_text_preview: "ID CARD JOHN",
    suggested_metadata: {
      category: "identitycards", document_name: "John ID", expiration_date: null,
      requires_alert: true, extracted_text: "ID CARD JOHN", financial_value: null, confidence_score: 0.6,
    },
    confidence_score: 0.6, review_reasons: ["expiration_unparsed"], created_at: "2026-10-17T10:00:00Z",
  },
  {
    id: "r-2", thread_id: "ing-2", family_member_id: "m-ann", document_id: "d-10",
    original_filename: "receipt.pdf", raw_text_preview: "RECEIPT",
    suggested_metadata: {
      category: "miscellaneous", document_name: "Receipt", expiration_date: null,
      requires_alert: false, extracted_text: "RECEIPT", financial_value: 42.5, confidence_score: 0.7,
    },
    confidence_score: 0.7, review_reasons: ["low_confidence"], created_at: "2026-10-17T11:00:00Z",
  },
];

export function sseBody(frames: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const frame of frames) controller.enqueue(encoder.encode(frame));
      controller.close();
    },
  });
}

export const chatFrames = [
  'event: filter\ndata: {"member_id":"m-john","member":"John","category":"identitycards","label":"Context Filtered: John | identitycards"}\n\n',
  'event: token\ndata: {"text":"John\'s passport "}\n\n',
  'event: token\ndata: {"text":"expires 2031-05-04."}\n\n',
  'event: sources\ndata: [{"document_id":"d-1","document_name":"John Passport","category":"identitycards","score":0.92}]\n\n',
  'event: done\ndata: {"conversation_id":"c-1"}\n\n',
];

export const handlers = [
  http.get(`${API}/api/me`, () => HttpResponse.json(me)),
  http.get(`${API}/api/vault/metrics`, () => HttpResponse.json(metrics)),
  http.get(`${API}/api/vault/categories`, () => HttpResponse.json(categories)),
  http.get(`${API}/api/vault/uploads`, () => HttpResponse.json([])),
  http.get(`${API}/api/vault/financial/options`, () => HttpResponse.json({
    subcategories: { income: ["Salary", "Bonus", "Interest"], expense: ["Groceries", "Rent", "Utilities"] },
    currencies: ["SGD", "USD", "INR", "NOK"],
  })),
  http.get(`${API}/readyz`, () =>
    HttpResponse.json({ status: "ok", checks: { postgres: "ok", redis: "ok", pinecone: "ok" } }),
  ),
  http.get(`${API}/api/vault/pending-reviews`, () => HttpResponse.json(reviews)),
  http.post(`${API}/api/vault/chat`, () =>
    new HttpResponse(sseBody(chatFrames), { headers: { "Content-Type": "text/event-stream" } }),
  ),
];

export const server = setupServer(...handlers);

export function apiError(status: number, code: string, message: string, details = {}) {
  return HttpResponse.json({ error: { code, message, details } }, { status });
}

/**
 * Vitest 5 cannot construct Requests whose body holds jsdom Blobs, so multipart calls are
 * intercepted at fetch level (other requests still go through MSW).
 */
export function interceptMultipart(path: string, respond: (form: FormData) => Response): FormData[] {
  const calls: FormData[] = [];
  const original = globalThis.fetch;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    if (url === `${API}${path}` && init?.body instanceof FormData) {
      calls.push(init.body);
      return respond(init.body);
    }
    return original(input, init);
  });
  return calls;
}

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
