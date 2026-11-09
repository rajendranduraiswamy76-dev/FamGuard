import { describe, expect, it } from "vitest";

import { BULK_TEMPLATE_HEADERS, buildTemplateCsv, csvField, toCsv } from "@/lib/csv";
import { isIsoDate, slugError, toSlug } from "@/lib/validation";

import { me } from "./msw";

describe("csv", () => {
  it("escapes per RFC 4180", () => {
    expect(csvField("plain")).toBe("plain");
    expect(csvField("a,b")).toBe('"a,b"');
    expect(csvField('say "hi"')).toBe('"say ""hi"""');
    expect(csvField("line\nbreak")).toBe('"line\nbreak"');
    expect(csvField(null)).toBe("");
    expect(toCsv([["a", 1], ["b", null]])).toBe("a,1\r\nb,\r\n");
  });

  it("builds the template with exact headers and real member ids", () => {
    const csv = buildTemplateCsv(me.members);
    const [header, first, second] = csv.trim().split("\r\n");
    expect(header).toBe(BULK_TEMPLATE_HEADERS.join(","));
    expect(header).toBe(
      "FamilyMemberID,Category,DocumentName,ExtractedText,ExpirationDate,FinancialValue",
    );
    expect(first.startsWith("m-ann,identitycards,Ann Passport,")).toBe(true);
    expect(first).toContain('"Passport, valid until 2031-05-04"');
    expect(second).toContain('"Utility bill ""Metro Power"", total due 120.50"');
    expect(second.startsWith("m-john,expense,")).toBe(true);
  });

  it("still produces headers with no members", () => {
    expect(buildTemplateCsv([]).trim()).toBe(BULK_TEMPLATE_HEADERS.join(","));
  });
});

describe("validation", () => {
  it.each([
    ["Vehicle Docs", "vehicle-docs"],
    ["  Ünïcode — Café  ", "unicode-cafe"],
    ["--Pets!!", "pets"],
  ])("slugifies %s", (input, slug) => {
    expect(toSlug(input)).toBe(slug);
  });

  it("validates slugs like the backend", () => {
    expect(slugError("vehicle-docs")).toBeNull();
    expect(slugError("Vehicle Docs")).toMatch(/lowercase/);
    expect(slugError("a--b")).toMatch(/lowercase/);
    expect(slugError("")).toMatch(/required/);
    expect(slugError("a".repeat(49))).toMatch(/48/);
  });

  it("checks real calendar dates", () => {
    expect(isIsoDate("2030-02-28")).toBe(true);
    expect(isIsoDate("2030-02-30")).toBe(false);
    expect(isIsoDate("30/02/2030")).toBe(false);
  });
});
