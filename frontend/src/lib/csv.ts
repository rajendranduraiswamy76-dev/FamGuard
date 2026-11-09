import type { Member } from "./types";

export const BULK_TEMPLATE_HEADERS = [
  "FamilyMemberID",
  "Category",
  "DocumentName",
  "ExtractedText",
  "ExpirationDate",
  "FinancialValue",
] as const;

/** RFC 4180: quote fields containing separators, quotes or line breaks; double inner quotes. */
export function csvField(value: string | number | null | undefined): string {
  const text = value === null || value === undefined ? "" : String(value);
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

export function toCsv(rows: ReadonlyArray<ReadonlyArray<string | number | null>>): string {
  return rows.map((row) => row.map(csvField).join(",")).join("\r\n") + "\r\n";
}

export function buildTemplateCsv(members: Member[]): string {
  const first = members[0];
  const second = members[1] ?? first;
  const rows: (string | number | null)[][] = [[...BULK_TEMPLATE_HEADERS]];
  if (first) {
    rows.push([
      first.id,
      "identitycards",
      `${first.display_name} Passport`,
      "Passport, valid until 2031-05-04",
      "2031-05-04",
      null,
    ]);
  }
  if (second) {
    rows.push([
      second.id,
      "expense",
      "Electric bill, March",
      'Utility bill "Metro Power", total due 120.50',
      "",
      120.5,
    ]);
  }
  return toCsv(rows);
}

export function downloadTextFile(filename: string, content: string, mime = "text/csv"): void {
  const blob = new Blob([content], { type: `${mime};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}
