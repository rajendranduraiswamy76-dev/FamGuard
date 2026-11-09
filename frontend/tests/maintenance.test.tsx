import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse, delay } from "msw";
import { describe, expect, it } from "vitest";

import { UploadsView } from "@/app/components/UploadsView";
import type { UploadStatus } from "@/lib/types";

import { API, apiError, me, server } from "./msw";
import { renderWithApp } from "./render";

const document: UploadStatus = {
  document_details: "",
  thread_id: "t-1", document_id: "doc-1", family_member_id: "m-ann",
  status: "completed", filename: "policy.pdf", document_name: "Policy",
  category: "medical", expiration_date: null, requires_alert: false,
};

describe("Upload maintenance", () => {
  it("does not fetch or show records until filtered, and hides them when cleared", async () => {
    let calls = 0;
    server.use(http.get(`${API}/api/vault/uploads`, () => {
      calls += 1; return HttpResponse.json([document]);
    }));
    renderWithApp(<UploadsView />);
    const owner = await screen.findByLabelText("Filter by document owner");
    expect(screen.getByText("Select an owner or category filter to show uploaded documents.")).toBeInTheDocument();
    expect(calls).toBe(0);
    expect(screen.queryByText("Policy")).not.toBeInTheDocument();
    await userEvent.selectOptions(owner, "m-ann");
    expect(await screen.findByText("Policy")).toBeInTheDocument();
    const count = calls;
    await userEvent.selectOptions(screen.getByLabelText("Filter by document owner"), "");
    expect(screen.queryByText("Policy")).not.toBeInTheDocument();
    expect(calls).toBe(count);
  });

  it("opens a correction review only after confirmation and preserves retry on errors", async () => {
    let status: UploadStatus["status"] = "completed";
    let fail = true;
    let calls = 0;
    server.use(
      http.get(`${API}/api/vault/uploads`, () => HttpResponse.json([{ ...document, status }])),
      http.post(`${API}/api/vault/documents/doc-1/review`, () => {
        calls += 1;
        if (fail) return apiError(503, "unavailable", "Review unavailable");
        status = "review_required";
        return HttpResponse.json({ ...document, status, thread_id: "review-new" });
      }),
    );
    renderWithApp(<UploadsView />);
    await userEvent.selectOptions(await screen.findByLabelText("Filter by document owner"), "m-ann");
    await userEvent.click(await screen.findByRole("button", { name: "Review extraction policy.pdf" }));
    expect(screen.getByRole("region", { name: "Confirm extraction review" }))
      .toHaveTextContent("Removes existing search entries");
    expect(calls).toBe(0);
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(calls).toBe(0);
    await userEvent.click(screen.getByRole("button", { name: "Review extraction policy.pdf" }));
    expect(screen.getByLabelText("Filter by document owner")).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Confirm review" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByText("Policy")).toBeInTheDocument();
    fail = false;
    await userEvent.click(screen.getByRole("button", { name: "Confirm review" }));
    expect(await screen.findByRole("link", { name: "Open Reviews" })).toHaveAttribute("href", "/reviews");
    await waitFor(() => expect(screen.getByText("Needs approval - not searchable yet")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Delete policy.pdf" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Review extraction policy.pdf" })).not.toBeInTheDocument();
    expect(calls).toBe(2);
  });

  it("filters by owner and category on the API and preserves document actions", async () => {
    const calls: URLSearchParams[] = [];
    server.use(http.get(`${API}/api/vault/uploads`, ({ request }) => {
      const params = new URL(request.url).searchParams;
      calls.push(params);
      if (params.get("category") === "identitycards") return HttpResponse.json([]);
      return HttpResponse.json([{
        ...document, family_member_id: params.get("family_member_id") || "m-ann",
        document_details: "Policy reference SAVED-42", status: "review_required",
      }]);
    }));
    renderWithApp(<UploadsView />);
    const maintenance = within(await screen.findByRole("region", { name: "Maintenance" }));
    expect(maintenance.queryByText("Recent documents")).not.toBeInTheDocument();
    await userEvent.selectOptions(maintenance.getByLabelText("Filter by document owner"), "m-john");
    await userEvent.selectOptions(maintenance.getByLabelText("Filter by category"), "medical");
    expect(await maintenance.findByText(/Policy reference SAVED-42/)).toBeInTheDocument();
    await waitFor(() => expect(calls.at(-1)?.get("category")).toBe("medical"));
    expect(calls.at(-1)?.get("family_member_id")).toBe("m-john");
    expect(calls.at(-1)?.get("offset")).toBe("0");
    expect(maintenance.getByRole("link", { name: "Review document" })).toHaveAttribute("href", "/reviews");
    expect(maintenance.getByRole("button", { name: "Open original policy.pdf" })).toBeEnabled();
    await userEvent.selectOptions(maintenance.getByLabelText("Filter by category"), "identitycards");
    expect(await maintenance.findByText("No uploaded documents match the selected filters.")).toBeInTheDocument();
    expect(maintenance.queryByText("Policy")).not.toBeInTheDocument();
  });

  it("pages beyond 50 uploads and resets pagination when filters change", async () => {
    const calls: URLSearchParams[] = [];
    server.use(http.get(`${API}/api/vault/uploads`, ({ request }) => {
      const params = new URL(request.url).searchParams;
      calls.push(params);
      const offset = Number(params.get("offset") || 0);
      const count = offset === 0 ? 51 : 1;
      return HttpResponse.json(Array.from({ length: count }, (_, index) => ({
        ...document, thread_id: `t-${offset + index}`, document_id: `d-${offset + index}`,
        document_name: `Document ${offset + index}`,
      })));
    }));
    renderWithApp(<UploadsView />);
    const maintenance = within(await screen.findByRole("region", { name: "Maintenance" }));
    await userEvent.selectOptions(maintenance.getByLabelText("Filter by document owner"), "m-ann");
    await maintenance.findByText("Document 0");
    expect(maintenance.getAllByRole("listitem")).toHaveLength(50);
    await userEvent.click(maintenance.getByRole("button", { name: "Next" }));
    expect(await maintenance.findByText("Document 50")).toBeInTheDocument();
    expect(maintenance.queryByText("Document 0")).not.toBeInTheDocument();
    expect(maintenance.getByRole("button", { name: "Next" })).toBeDisabled();
    await userEvent.selectOptions(maintenance.getByLabelText("Filter by category"), "medical");
    await maintenance.findByText("Document 0");
    expect(calls.at(-1)?.get("offset")).toBe("0");
    expect(calls.at(-1)?.get("category")).toBe("medical");
    expect(maintenance.getByRole("button", { name: "Previous" })).toBeDisabled();
  });

  it("restricts the owner filter to the current non-admin member", async () => {
    server.use(http.get(`${API}/api/me`, () => HttpResponse.json({
      ...me, member: { ...me.member, role: "member" },
    })));
    renderWithApp(<UploadsView />);
    const owner = await screen.findByLabelText("Filter by document owner");
    expect(within(owner).getAllByRole("option").map((option) => option.textContent))
      .toEqual(["All available owners", "Ann"]);
  });

  it("requires confirmation, supports cancel, and removes the document only after success", async () => {
    let documents = [document];
    let calls = 0;
    server.use(
      http.get(`${API}/api/vault/uploads`, () => HttpResponse.json(documents)),
      http.delete(`${API}/api/vault/documents/doc-1`, async () => {
        calls += 1;
        await delay(80);
        documents = [];
        return new HttpResponse(null, { status: 204 });
      }),
    );
    renderWithApp(<UploadsView />);
    expect(await screen.findByRole("region", { name: "Maintenance" })).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Filter by document owner"), "m-ann");
    await userEvent.click(await screen.findByRole("button", { name: "Delete policy.pdf" }));
    expect(screen.getByRole("region", { name: "Confirm document deletion" })).toHaveTextContent("cannot be undone");
    expect(calls).toBe(0);
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(calls).toBe(0);
    expect(screen.getByText("Policy")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Delete policy.pdf" }));
    await userEvent.click(screen.getByRole("button", { name: "Confirm delete" }));
    expect(screen.getByRole("button", { name: "Deleting..." })).toBeDisabled();
    expect(await screen.findByText("Deleted policy.pdf.")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText("Policy")).not.toBeInTheDocument());
    expect(calls).toBe(1);
  });

  it("preserves the document and offers retry after a deletion failure", async () => {
    let fail = true;
    let documents = [document];
    server.use(
      http.get(`${API}/api/vault/uploads`, () => HttpResponse.json(documents)),
      http.delete(`${API}/api/vault/documents/doc-1`, () => {
        if (fail) return apiError(409, "conflict", "Document is still processing");
        documents = [];
        return new HttpResponse(null, { status: 204 });
      }),
    );
    renderWithApp(<UploadsView />);
    await userEvent.selectOptions(await screen.findByLabelText("Filter by document owner"), "m-ann");
    await userEvent.click(await screen.findByRole("button", { name: "Delete policy.pdf" }));
    await userEvent.click(screen.getByRole("button", { name: "Confirm delete" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Document is still processing");
    expect(screen.getByText("Policy")).toBeInTheDocument();
    fail = false;
    await userEvent.click(screen.getByRole("button", { name: "Confirm delete" }));
    expect(await screen.findByText("Deleted policy.pdf.")).toBeInTheDocument();
  });

  it.each(["processing", "review_required"] as const)("disables deletion for %s documents", async (status) => {
    server.use(http.get(`${API}/api/vault/uploads`, () => HttpResponse.json([{ ...document, status }])));
    renderWithApp(<UploadsView />);
    await userEvent.selectOptions(await screen.findByLabelText("Filter by document owner"), "m-ann");
    expect(await screen.findByRole("button", { name: "Delete policy.pdf" })).toBeDisabled();
    expect(screen.getByText(/Deletion is available after processing finishes/)).toBeInTheDocument();
  });
});
