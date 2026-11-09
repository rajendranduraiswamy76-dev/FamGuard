import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { ChatAgent, filterLabel } from "@/app/components/ChatAgent";

import { API, interceptMultipart, jsonResponse, server, sseBody } from "./msw";
import { renderWithApp } from "./render";

async function ask(question: string) {
  const input = await screen.findByLabelText("Message");
  await userEvent.type(input, question);
  await userEvent.keyboard("{Enter}");
}

describe("filterLabel", () => {
  it("renders full and partial filters", () => {
    expect(filterLabel({ member_id: "1", member: "John", category: "identitycards" })).toBe(
      "Context Filtered: John | identitycards",
    );
    expect(filterLabel({ member_id: null, member: null, category: null })).toBe(
      "Context Filtered: All members | All categories",
    );
  });
});

describe("ChatAgent", () => {
  it("streams the answer with the filter banner and sources", async () => {
    renderWithApp(<ChatAgent />);
    await ask("When does John's passport expire?");

    expect(await screen.findByTestId("context-filter")).toHaveTextContent(
      "Context Filtered: John | identitycards",
    );
    await waitFor(() =>
      expect(screen.getByTestId("assistant-text")).toHaveTextContent("John's passport expires 2031-05-04."),
    );
    expect(within(screen.getByRole("list", { name: "Sources" })).getByText(/John Passport/)).toBeInTheDocument();
    expect(screen.getByRole("log")).toHaveAttribute("aria-live", "polite");
    expect(screen.getByLabelText("Message")).toHaveValue("");
  });

  it("shows the partial filter label", async () => {
    server.use(
      http.post(`${API}/api/vault/chat`, () =>
        new HttpResponse(
          sseBody([
            'event: filter\ndata: {"member_id":null,"member":null,"category":"medical"}\n\n',
            'event: token\ndata: {"text":"ok"}\n\n',
            'event: done\ndata: {"conversation_id":null}\n\n',
          ]),
          { headers: { "Content-Type": "text/event-stream" } },
        ),
      ),
    );
    renderWithApp(<ChatAgent />);
    await ask("insurance?");
    expect(await screen.findByTestId("context-filter")).toHaveTextContent(
      "Context Filtered: All members | medical",
    );
  });

  it("renders server error events", async () => {
    server.use(
      http.post(`${API}/api/vault/chat`, () =>
        new HttpResponse(
          sseBody([
            'event: error\ndata: {"message":"Something went wrong while answering. Please retry."}\n\n',
            'event: done\ndata: {"conversation_id":null}\n\n',
          ]),
          { headers: { "Content-Type": "text/event-stream" } },
        ),
      ),
    );
    renderWithApp(<ChatAgent />);
    await ask("hello");
    expect(await screen.findByText(/Please retry/)).toBeInTheDocument();
  });

  it("explains rate limiting", async () => {
    server.use(
      http.post(`${API}/api/vault/chat`, () =>
        HttpResponse.json({ error: { code: "rate_limited", message: "Too many", details: {} } }, { status: 429 }),
      ),
    );
    renderWithApp(<ChatAgent />);
    await ask("hello");
    expect(await screen.findByText(/sending messages too quickly/)).toBeInTheDocument();
  });

  it("does not send on Shift+Enter", async () => {
    renderWithApp(<ChatAgent />);
    const input = await screen.findByLabelText("Message");
    await userEvent.type(input, "line one{Shift>}{Enter}{/Shift}line two");
    expect(input).toHaveValue("line one\nline two");
    expect(screen.queryByTestId("assistant-text")).not.toBeInTheDocument();
  });

  it("uploads dropped files and tracks status until review", async () => {
    let polls = 0;
    const calls = interceptMultipart("/api/vault/upload", () =>
      jsonResponse({ uploads: [{ thread_id: "ing-7", document_id: "d-7", filename: "id.jpg" }] }, 202),
    );
    server.use(
      http.get(`${API}/api/vault/uploads/ing-7`, () => {
        polls += 1;
        return HttpResponse.json({
          thread_id: "ing-7", document_id: "d-7", family_member_id: "m-ann",
          status: polls < 2 ? "processing" : "review_required", filename: "id.jpg",
          category: "miscellaneous", document_name: "id.jpg", expiration_date: null,
          requires_alert: false, pending_review_id: polls < 2 ? null : "r-7", error: null,
        });
      }),
    );
    renderWithApp(<ChatAgent uploadPollMs={10} />);
    await screen.findByLabelText("Message");
    await waitFor(() => expect(screen.getByRole("region", { name: "Vault assistant" })).toBeInTheDocument());
    // Wait for /api/me so the member id is known before dropping.
    await new Promise((r) => setTimeout(r, 50));

    fireEvent.drop(screen.getByRole("region", { name: "Vault assistant" }), {
      dataTransfer: { files: [new File(["img"], "id.jpg", { type: "image/jpeg" })] },
    });

    const list = await screen.findByRole("list", { name: "Attachments" });
    expect(await within(list).findByText("Needs review")).toBeInTheDocument();
    expect(within(list).getByRole("link", { name: "Review" })).toHaveAttribute("href", "/reviews");
    const sent = calls[0];
    expect(sent.get("source")).toBe("chat");
    expect(sent.get("family_member_id")).toBe("m-ann");
  });
});
