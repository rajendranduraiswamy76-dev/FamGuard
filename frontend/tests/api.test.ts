import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { ApiError, createApiClient } from "@/lib/api";

import { API, apiError, chatFrames, interceptMultipart, jsonResponse, server, sseBody } from "./msw";

const client = createApiClient(async () => "tok-123", API);

describe("api client", () => {
  it("deletes a document using its encoded id and accepts an empty 204 response", async () => {
    let path: string | null = null;
    server.use(http.delete(`${API}/api/vault/documents/:id`, ({ request }) => {
      path = new URL(request.url).pathname;
      return new HttpResponse(null, { status: 204 });
    }));
    await expect(client.deleteDocument("doc/id")).resolves.toBeUndefined();
    expect(path).toBe("/api/vault/documents/doc%2Fid");
  });
  it("sends the bearer token", async () => {
    let auth: string | null = null;
    server.use(
      http.get(`${API}/api/vault/metrics`, ({ request }) => {
        auth = request.headers.get("authorization");
        return HttpResponse.json({ documents_total: 1 });
      }),
    );
    await client.metrics();
    expect(auth).toBe("Bearer tok-123");
  });

  it("refuses to call the API without a session token", async () => {
    const signedOut = createApiClient(async () => null, API);
    await expect(signedOut.metrics()).rejects.toMatchObject({ status: 401 });
  });

  it("maps the error envelope to ApiError", async () => {
    server.use(http.get(`${API}/api/me`, () => apiError(401, "unauthorized", "Token expired")));
    const error = await client.me().catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 401, code: "unauthorized", message: "Token expired" });
  });

  it("surfaces validation details", async () => {
    server.use(
      http.post(`${API}/api/vault/categories`, () =>
        apiError(422, "validation_error", "Request validation failed", { errors: [{ msg: "bad" }] }),
      ),
    );
    const error = (await client.createCategory("x", "X").catch((e: unknown) => e)) as ApiError;
    expect(error.status).toBe(422);
    expect(error.details).toEqual({ errors: [{ msg: "bad" }] });
  });

  it("treats network failures and 503 as 'unavailable' (cold start)", async () => {
    server.use(http.get(`${API}/api/me`, () => HttpResponse.error()));
    const network = (await client.me().catch((e: unknown) => e)) as ApiError;
    expect(network.status).toBe(0);
    expect(network.isUnavailable).toBe(true);

    server.use(http.get(`${API}/api/me`, () => new HttpResponse("Bad gateway", { status: 502 })));
    const gateway = (await client.me().catch((e: unknown) => e)) as ApiError;
    expect(gateway.isUnavailable).toBe(true);
    expect(gateway.code).toBe("http_error");
  });

  it("reports degraded readiness instead of throwing on 503", async () => {
    server.use(
      http.get(`${API}/readyz`, () =>
        HttpResponse.json({ status: "degraded", checks: { redis: "error" } }, { status: 503 }),
      ),
    );
    expect(await client.readiness()).toEqual({ status: "degraded", checks: {} });
  });

  it("uploads multipart with member and source", async () => {
    const calls = interceptMultipart("/api/vault/upload", () =>
      jsonResponse({ uploads: [{ thread_id: "t1", document_id: "d1", filename: "a.png" }] }, 202),
    );
    const uploads = await client.upload([new File(["x"], "a.png")], "m-1", "chat");
    expect(uploads).toHaveLength(1);
    const sent = calls[0];
    expect(sent.get("family_member_id")).toBe("m-1");
    expect(sent.get("source")).toBe("chat");
    expect((sent.get("files") as File).name).toBe("a.png");
  });

  it("streams chat events", async () => {
    server.use(
      http.post(`${API}/api/vault/chat`, () =>
        new HttpResponse(sseBody(chatFrames), { headers: { "Content-Type": "text/event-stream" } }),
      ),
    );
    const names: string[] = [];
    for await (const evt of client.chat("hi", "c1")) names.push(evt.event);
    expect(names).toEqual(["filter", "token", "token", "sources", "done"]);
  });
});
