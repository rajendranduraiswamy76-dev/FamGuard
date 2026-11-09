import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { BulkUpload } from "@/app/components/BulkUpload";
import { CategoryForm } from "@/app/components/CategoryForm";
import { StaticTokenProvider } from "@/lib/auth";
import type { Category } from "@/lib/types";

import { API, apiError, categories, interceptMultipart, jsonResponse, me, server } from "./msw";

function Harness({ isAdmin = true }: { isAdmin?: boolean }) {
  const [list, setList] = useState<Category[]>(categories);
  return (
    <StaticTokenProvider>
      <CategoryForm categories={list} isAdmin={isAdmin} onChange={(fn) => setList(fn)} />
    </StaticTokenProvider>
  );
}

describe("CategoryForm", () => {
  it("auto-generates a slug preview and submits", async () => {
    let body: unknown;
    server.use(
      http.post(`${API}/api/vault/categories`, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json(
          { id: "c-9", category_slug: "vehicle-docs", category_display_name: "Vehicle Docs", is_baseline: false },
          { status: 201 },
        );
      }),
    );
    render(<Harness />);
    await userEvent.type(screen.getByLabelText("Category display name"), "Vehicle Docs");
    expect(screen.getByLabelText("Slug")).toHaveValue("vehicle-docs");
    await userEvent.click(screen.getByRole("button", { name: "Add category" }));

    expect(await screen.findByText("Added “Vehicle Docs”.")).toBeInTheDocument();
    expect(body).toEqual({ category_slug: "vehicle-docs", category_display_name: "Vehicle Docs" });
    const chips = within(screen.getByRole("list", { name: "Available categories" }));
    expect(chips.getByText("vehicle-docs")).toBeInTheDocument();
  });

  it("blocks invalid manual slugs", async () => {
    render(<Harness />);
    await userEvent.type(screen.getByLabelText("Category display name"), "Boats");
    const slug = screen.getByLabelText("Slug");
    await userEvent.clear(slug);
    await userEvent.type(slug, "Bad Slug");
    expect(slug).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByText(/lowercase letters and numbers/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add category" })).toBeDisabled();
  });

  it("rolls back the optimistic item on 409", async () => {
    server.use(
      http.post(`${API}/api/vault/categories`, () => apiError(409, "conflict", "exists")),
    );
    render(<Harness />);
    await userEvent.type(screen.getByLabelText("Category display name"), "Vehicle");
    await userEvent.click(screen.getByRole("button", { name: "Add category" }));
    expect(await screen.findByText(/already exists/)).toBeInTheDocument();
    const chips = within(screen.getByRole("list", { name: "Available categories" }));
    expect(chips.queryByText("vehicle")).not.toBeInTheDocument();
  });

  it("hides the form for non-admins", () => {
    render(<Harness isAdmin={false} />);
    expect(screen.queryByLabelText("Category display name")).not.toBeInTheDocument();
    expect(screen.getByText(/Only family admins/)).toBeInTheDocument();
  });
});

describe("BulkUpload", () => {
  it("downloads a CSV template built from the family roster", async () => {
    let csv = "";
    let mime = "";
    const createObjectURL = vi.fn((blob: Blob) => {
      mime = blob.type;
      const reader = new FileReader();
      reader.onload = () => (csv = String(reader.result));
      reader.readAsText(blob);
      return "blob:template";
    });
    Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() });
    let downloadName = "";
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(function (this: HTMLAnchorElement) {
        downloadName = this.download;
      });

    render(
      <StaticTokenProvider>
        <BulkUpload members={me.members} />
      </StaticTokenProvider>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Download sample CSV" }));
    expect(click).toHaveBeenCalled();
    expect(downloadName).toBe("famguard-template.csv");
    expect(mime).toContain("text/csv");
    await waitFor(() =>
      expect(csv.split("\r\n")[0]).toBe(
        "FamilyMemberID,Category,DocumentName,ExtractedText,ExpirationDate,FinancialValue",
      ),
    );
    expect(csv).toContain("m-ann,identitycards");
  });

  it("uploads a dropped CSV and shows row errors", async () => {
    const calls = interceptMultipart("/api/vault/bulk-upload", () =>
      jsonResponse({
        accepted: 197,
        rejected: 3,
        errors: [
          { row: 12, reason: "Unknown category 'spaceship'" },
          { row: 52, reason: "ExpirationDate must be YYYY-MM-DD" },
          { row: 152, reason: "Unknown family member" },
        ],
        errors_truncated: false,
      }),
    );
    render(
      <StaticTokenProvider>
        <BulkUpload members={me.members} />
      </StaticTokenProvider>,
    );
    const file = new File(["FamilyMemberID\n"], "family.csv", { type: "text/csv" });
    fireEvent.drop(screen.getByRole("button", { name: /Drop a CSV here/ }), { dataTransfer: { files: [file] } });

    expect(await screen.findByText("197 imported")).toBeInTheDocument();
    expect(screen.getByText("3 rejected")).toBeInTheDocument();
    const rows = screen.getAllByRole("row");
    expect(rows).toHaveLength(4);
    expect(within(rows[1]).getByText("12")).toBeInTheDocument();
    expect((calls[0].get("file") as File).name).toBe("family.csv");
  });

  it("rejects non-CSV and oversized files client-side", async () => {
    render(
      <StaticTokenProvider>
        <BulkUpload members={me.members} />
      </StaticTokenProvider>,
    );
    const input = screen.getByTestId("bulk-file-input");
    await userEvent.upload(input, new File(["x"], "data.xlsx"), { applyAccept: false });
    expect(await screen.findByRole("alert")).toHaveTextContent("Please choose a .csv file.");

    const big = new File(["x"], "big.csv");
    Object.defineProperty(big, "size", { value: 6 * 1024 * 1024 });
    await userEvent.upload(input, big);
    expect(await screen.findByRole("alert")).toHaveTextContent("5 MB or smaller");
  });
});
