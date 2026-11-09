import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { validateCorrection } from "@/app/components/ReviewCard";
import { ReviewsView } from "@/app/components/ReviewsView";

import { API, reviews, server } from "./msw";
import { renderWithApp } from "./render";

describe("validateCorrection", () => {
  const base = {
    category: "identitycards", document_name: "ID", expiration_date: "2030-01-01",
    requires_alert: true, extracted_text: "x", financial_value: null,
  };
  it("requires a valid date when alerts are on", () => {
    expect(validateCorrection(base)).toBeNull();
    expect(validateCorrection({ ...base, expiration_date: null })).toMatch(/need an expiry date/);
    expect(validateCorrection({ ...base, expiration_date: "2030-02-30" })).toMatch(/YYYY-MM-DD/);
    expect(validateCorrection({ ...base, document_name: " " })).toMatch(/name is required/);
    expect(validateCorrection({ ...base, requires_alert: false, expiration_date: null })).toBeNull();
  });
});

describe("ReviewsView", () => {
  it("explains manual verification and submits corrected expiry and extracted dates together", async () => {
    let body: Record<string, unknown> | null = null;
    server.use(
      http.get(`${API}/api/vault/pending-reviews`, () => HttpResponse.json([{
        ...reviews[0], confidence_score: 1, review_reasons: ["user_requested"],
      }])),
      http.post(`${API}/api/vault/resolve-review`, async ({ request }) => {
        body = await request.json() as Record<string, unknown>;
        return HttpResponse.json({ thread_id: "ing-1", status: "approved" }, { status: 202 });
      }),
    );
    renderWithApp(<ReviewsView />);
    const card = within((await screen.findAllByRole("article"))[0]);
    expect(card.getByText("Extraction correction requested")).toBeInTheDocument();
    expect(card.getByText(/Confidence is the original model estimate, not verification/))
      .toBeInTheDocument();
    await userEvent.type(card.getByLabelText("Expiry date"), "2036-08-20");
    await userEvent.clear(card.getByLabelText("Extracted text"));
    const text = "Birth: 1985-04-12\nIssue: 2026-08-20\nExpiry: 2036-08-20";
    await userEvent.type(card.getByLabelText("Extracted text"), text);
    await userEvent.click(card.getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(body).toMatchObject({
      decision: "approve",
      corrected_metadata: { expiration_date: "2036-08-20", extracted_text: text },
    }));
  });

  it("renders pending reviews with reasons and confidence", async () => {
    renderWithApp(<ReviewsView />);
    const cards = await screen.findAllByRole("article");
    expect(cards).toHaveLength(2);
    expect(within(cards[0]).getByText("Expiry date unreadable")).toBeInTheDocument();
    expect(within(cards[0]).getByText("60% confidence")).toBeInTheDocument();
    await waitFor(() => expect(within(cards[0]).getByText(/^John ·/)).toBeInTheDocument());
  });

  it("blocks approval without a date when alerts are on, then approves with corrections", async () => {
    let body: Record<string, unknown> | null = null;
    server.use(
      http.post(`${API}/api/vault/resolve-review`, async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ thread_id: "ing-1", status: "approved" }, { status: 202 });
      }),
    );
    renderWithApp(<ReviewsView />);
    const card = (await screen.findAllByRole("article"))[0];

    await userEvent.click(within(card).getByRole("button", { name: "Approve" }));
    expect(within(card).getByRole("alert")).toHaveTextContent("need an expiry date");
    expect(body).toBeNull();

    const date = within(card).getByLabelText("Expiry date");
    await userEvent.type(date, "2030-06-30");
    await userEvent.click(within(card).getByRole("button", { name: "Approve" }));

    await waitFor(() => expect(screen.getAllByRole("article")).toHaveLength(1));
    expect(screen.getByRole("status")).toHaveTextContent("Approved john-id.jpg");
    expect(body).toMatchObject({
      thread_id: "ing-1",
      decision: "approve",
      corrected_metadata: { expiration_date: "2030-06-30", requires_alert: true, category: "identitycards" },
    });
  });

  it("rejects without sending corrections", async () => {
    let body: Record<string, unknown> | null = null;
    server.use(
      http.post(`${API}/api/vault/resolve-review`, async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ thread_id: "ing-2", status: "rejected" }, { status: 202 });
      }),
    );
    renderWithApp(<ReviewsView />);
    const card = (await screen.findAllByRole("article"))[1];
    await userEvent.click(within(card).getByRole("button", { name: "Reject" }));
    await waitFor(() => expect(screen.getAllByRole("article")).toHaveLength(1));
    expect(body).toEqual({ thread_id: "ing-2", decision: "reject", corrected_metadata: null });
  });

  it("shows an empty state", async () => {
    server.use(http.get(`${API}/api/vault/pending-reviews`, () => HttpResponse.json([])));
    renderWithApp(<ReviewsView />);
    expect(await screen.findByText(/nothing needs review/)).toBeInTheDocument();
  });
});
