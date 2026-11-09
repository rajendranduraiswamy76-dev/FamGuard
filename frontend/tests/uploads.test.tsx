import { fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import {
  MAX_DOCUMENT_BYTES,
  validateDocumentFiles,
} from "@/app/components/DocumentUpload";
import { UploadsView } from "@/app/components/UploadsView";
import { UploadHistory } from "@/app/components/UploadHistory";
import type { UploadStatus } from "@/lib/types";

import { API, apiError, interceptMultipart, jsonResponse, me, server } from "./msw";
import { renderWithApp } from "./render";

describe("document validation", () => {
  it.each(["pdf", "docx", "xlsx", "jpg", "jpeg", "png", "webp", "heic", "heif"])(
    "accepts .%s documents",
    (extension) => {
      expect(validateDocumentFiles([new File(["x"], `document.${extension.toUpperCase()}`)])).toBeNull();
    },
  );

  it("enforces exact file count and size boundaries", () => {
    const file = new File(["x"], "document.pdf");
    Object.defineProperty(file, "size", { value: MAX_DOCUMENT_BYTES });
    expect(validateDocumentFiles(Array.from({ length: 10 }, () => file))).toBeNull();
    expect(validateDocumentFiles(Array.from({ length: 11 }, () => file))).toContain("at most 10");
    const big = new File(["x"], "big.pdf");
    Object.defineProperty(big, "size", { value: MAX_DOCUMENT_BYTES + 1 });
    expect(validateDocumentFiles([big])).toContain("10 MB or smaller");
    expect(validateDocumentFiles([])).toContain("at least one");
    expect(validateDocumentFiles([new File([], "empty.pdf")])).toContain("empty");
    expect(validateDocumentFiles([new File(["x"], "records.csv")])).toContain("Use CSV import");
    expect(validateDocumentFiles([file, new File(["x"], "legacy.doc")])).toContain("legacy.doc");
  });
});

describe("Uploads tab", () => {
  it("contains document upload and categories but financial tools are in their own tab", async () => {
    renderWithApp(<UploadsView />);
    expect(await screen.findByRole("region", { name: "Document upload" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Custom categories" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("Category")).toHaveValue("");
    const category = screen.getByLabelText("Category");
    const details = screen.getByRole("textbox", { name: "Document details" });
    expect(category.compareDocumentPosition(details) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(details).toHaveAttribute("maxlength", "2000");
    expect(screen.queryByRole("region", { name: "Income/expense data upload" })).not.toBeInTheDocument();
    expect(screen.queryByText("Bulk CSV import")).not.toBeInTheDocument();
  });

  const uploaded: UploadStatus = {
    thread_id: "t-old", document_id: "d-old", family_member_id: "m-ann",
    status: "processing", filename: "old.pdf", category: "miscellaneous",
    document_name: "Old policy", expiration_date: null, requires_alert: false,
    document_details: "Renewal reference POLICY-42",
  };

  describe("upload history", () => {
    it("loads previous uploads and polls until they are searchable", async () => {
      let status: UploadStatus["status"] = "processing";
      server.use(http.get(`${API}/api/vault/uploads`, () =>
        HttpResponse.json([{ ...uploaded, status }])));
      renderWithApp(<UploadHistory members={me.members} pollMs={40} />);
      await userEvent.selectOptions(screen.getByLabelText("Filter by document owner"), "m-ann");
      expect(await screen.findByText("Processing - not searchable yet")).toBeInTheDocument();
      expect(screen.getByText("Old policy")).toBeInTheDocument();
      expect(screen.getByText(/Renewal reference POLICY-42/)).toBeInTheDocument();
      expect(screen.getByText(/old.pdf · Ann · miscellaneous/)).toBeInTheDocument();
      status = "completed";
      expect(await screen.findByText("Indexed - ready to search")).toBeInTheDocument();
      expect(screen.getByRole("link", { name: "Search vault" })).toHaveAttribute("href", "/");
    });

    it("shows approval requirements and processing errors", async () => {
      server.use(http.get(`${API}/api/vault/uploads`, () =>
        HttpResponse.json([
          { ...uploaded, status: "review_required" },
          { ...uploaded, thread_id: "t-failed", document_id: "d-failed", status: "failed", error: "Cannot parse document" },
        ])));
      renderWithApp(<UploadHistory members={me.members} pollMs={10_000} />);
      await userEvent.selectOptions(screen.getByLabelText("Filter by document owner"), "m-ann");
      expect(await screen.findByText("Needs approval - not searchable yet")).toBeInTheDocument();
      expect(screen.getByRole("link", { name: "Review document" })).toHaveAttribute("href", "/reviews");
      expect(screen.getByText("Cannot parse document")).toBeInTheDocument();
      expect(screen.getByText(/select the original file to upload again/)).toBeInTheDocument();
    });

    it("surfaces status and original file failures", async () => {
      server.use(http.get(`${API}/api/vault/uploads`, () => apiError(500, "error", "Status unavailable")));
      renderWithApp(<UploadHistory members={me.members} />);
      await userEvent.selectOptions(screen.getByLabelText("Filter by document owner"), "m-ann");
      expect(await screen.findByRole("alert")).toHaveTextContent("Status unavailable");
      server.use(
        http.get(`${API}/api/vault/uploads`, () => HttpResponse.json([{ ...uploaded, status: "completed" }])),
        http.get(`${API}/api/vault/documents/d-old/url`, () => apiError(404, "not_found", "Original file missing")),
      );
      await userEvent.click(screen.getByRole("button", { name: "Refresh uploads" }));
      await userEvent.click(await screen.findByRole("button", { name: "Open original old.pdf" }));
      expect(await screen.findByRole("alert")).toHaveTextContent("Original file missing");
    });
  });

  it("uploads multiple files to FastAPI with the selected owner and upload source", async () => {
    const calls = interceptMultipart("/api/vault/upload", (form) =>
      jsonResponse({
        uploads: form.getAll("files").map((entry, i) => ({
          filename: (entry as File).name, document_id: `d-${i}`, thread_id: `t-${i}`,
        })),
      }, 202),
    );
    renderWithApp(<UploadsView />);
    await userEvent.selectOptions(await screen.findByLabelText("Document owner"), "m-john");
    await userEvent.selectOptions(screen.getByLabelText("Category"), "medical");
    await userEvent.type(screen.getByLabelText("Document details"), "  Annual coverage POLICY-42  ");
    await userEvent.upload(screen.getByLabelText("Choose documents"), [
      new File(["pdf"], "passport.pdf", { type: "application/pdf" }),
      new File(["docx"], "policy.docx"),
      new File(["xlsx"], "accounts.xlsx"),
      new File(["image"], "card.heic"),
    ]);
    await userEvent.click(screen.getByRole("button", { name: "Upload documents" }));
    expect(await screen.findByText("4 documents accepted for processing.")).toBeInTheDocument();
    expect(calls).toHaveLength(1);
    expect(calls[0].get("family_member_id")).toBe("m-john");
    expect(calls[0].get("source")).toBe("upload");
    expect(calls[0].get("category")).toBe("medical");
    expect(calls[0].get("document_details")).toBe("Annual coverage POLICY-42");
    expect(screen.getByLabelText("Document details")).toHaveValue("");
    expect(calls[0].getAll("files").map((file) => (file as File).name))
      .toEqual(["passport.pdf", "policy.docx", "accounts.xlsx", "card.heic"]);
    expect(screen.getByRole("link", { name: "Approval Reviews" })).toHaveAttribute("href", "/reviews");
    expect(screen.queryByRole("list", { name: "Selected documents" })).not.toBeInTheDocument();
  });

  it("supports dropped files and removing a selection", async () => {
    renderWithApp(<UploadsView />);
    const drop = await screen.findByRole("button", { name: /Drop documents here/ });
    fireEvent.drop(drop, {
      dataTransfer: { files: [new File(["x"], "one.png"), new File(["x"], "two.pdf")] },
    });
    await userEvent.click(screen.getByRole("button", { name: "Remove one.png" }));
    expect(within(screen.getByRole("list", { name: "Selected documents" })).getAllByRole("listitem")).toHaveLength(1);
    await userEvent.click(screen.getByRole("button", { name: "Remove two.pdf" }));
    expect(screen.getByRole("button", { name: "Upload documents" })).toBeDisabled();
  });

  it("rejects an invalid dropped batch without calling the API", async () => {
    const calls = interceptMultipart("/api/vault/upload", () => jsonResponse({ uploads: [] }, 202));
    renderWithApp(<UploadsView />);
    fireEvent.drop(await screen.findByRole("button", { name: /Drop documents here/ }), {
      dataTransfer: { files: [new File(["x"], "good.pdf"), new File(["x"], "bad.exe")] },
    });
    expect(await screen.findByRole("alert")).toHaveTextContent("bad.exe");
    expect(screen.getByRole("button", { name: "Upload documents" })).toBeDisabled();
    expect(calls).toHaveLength(0);
  });

  it("surfaces API failures and keeps files for retry", async () => {
    let fail = true;
    interceptMultipart("/api/vault/upload", () =>
      fail
        ? jsonResponse({ error: { code: "duplicate", message: "Document already exists" } }, 409)
        : jsonResponse({ uploads: [{ filename: "id.png", document_id: "d-1", thread_id: "t-1" }] }, 202),
    );
    renderWithApp(<UploadsView />);
    await userEvent.upload(await screen.findByLabelText("Choose documents"), new File(["x"], "id.png"));
    await userEvent.type(screen.getByLabelText("Document details"), "Travel renewal");
    await userEvent.click(screen.getByRole("button", { name: "Upload documents" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Document already exists");
    expect(screen.getByRole("list", { name: "Selected documents" })).toHaveTextContent("id.png");
    expect(screen.getByLabelText("Document details")).toHaveValue("Travel renewal");
    fail = false;
    await userEvent.click(screen.getByRole("button", { name: "Upload documents" }));
    expect(await screen.findByText("1 document accepted for processing.")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Document details")).toHaveValue("");
  });

  it("omits blank document details and allows uploads without a description", async () => {
    const calls = interceptMultipart("/api/vault/upload", () =>
      jsonResponse({ uploads: [{ filename: "id.png", document_id: "d-1", thread_id: "t-1" }] }, 202));
    renderWithApp(<UploadsView />);
    await userEvent.upload(await screen.findByLabelText("Choose documents"), new File(["x"], "id.png"));
    await userEvent.type(screen.getByLabelText("Document details"), "   ");
    await userEvent.click(screen.getByRole("button", { name: "Upload documents" }));
    expect(await screen.findByText("1 document accepted for processing.")).toBeInTheDocument();
    expect(calls[0].has("document_details")).toBe(false);
  });

  it("restricts non-admins to their own documents and hides category creation", async () => {
    server.use(http.get(`${API}/api/me`, () => HttpResponse.json({
      ...me, member: { ...me.member, role: "member" },
    })));
    renderWithApp(<UploadsView />);
    const owner = await screen.findByLabelText("Document owner");
    expect(within(owner).getAllByRole("option")).toHaveLength(1);
    expect(owner).toHaveValue(me.member.id);
    expect(screen.queryByLabelText("Display name")).not.toBeInTheDocument();
  });

  it("offers retry when category loading fails without blocking uploads", async () => {
    server.use(http.get(`${API}/api/vault/categories`, () => apiError(500, "error", "Categories unavailable")));
    renderWithApp(<UploadsView />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Categories unavailable");
    expect(screen.getByLabelText("Choose documents")).toBeInTheDocument();
    server.use(http.get(`${API}/api/vault/categories`, () => HttpResponse.json([])));
    await userEvent.click(screen.getByRole("button", { name: "Retry categories" }));
    expect(screen.getByLabelText("Category")).toHaveValue("");
  });
});
