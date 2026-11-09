import { describe, expect, it } from "vitest";

import { parsePublicEnv } from "@/lib/env";

describe("parsePublicEnv", () => {
  it("accepts test mode without Clerk and trims trailing slashes", () => {
    const env = parsePublicEnv({
      NEXT_PUBLIC_API_BASE_URL: "https://api.example.com//",
      NEXT_PUBLIC_AUTH_MODE: "test",
    });
    expect(env.NEXT_PUBLIC_API_BASE_URL).toBe("https://api.example.com");
    expect(env.NEXT_PUBLIC_AUTH_MODE).toBe("test");
  });

  it("defaults to clerk mode and then requires a publishable key", () => {
    expect(() => parsePublicEnv({ NEXT_PUBLIC_API_BASE_URL: "https://api.example.com" })).toThrow(
      /NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY is required/,
    );
    const env = parsePublicEnv({
      NEXT_PUBLIC_API_BASE_URL: "https://api.example.com",
      NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: "pk_test_123",
    });
    expect(env.NEXT_PUBLIC_AUTH_MODE).toBe("clerk");
  });

  it("fails fast when the API URL is missing or invalid", () => {
    expect(() => parsePublicEnv({ NEXT_PUBLIC_AUTH_MODE: "test" })).toThrow(
      /NEXT_PUBLIC_API_BASE_URL is required/,
    );
    expect(() =>
      parsePublicEnv({ NEXT_PUBLIC_API_BASE_URL: "not a url", NEXT_PUBLIC_AUTH_MODE: "test" }),
    ).toThrow(/absolute URL/);
  });

  it("rejects unknown auth modes", () => {
    expect(() =>
      parsePublicEnv({ NEXT_PUBLIC_API_BASE_URL: "http://x.test", NEXT_PUBLIC_AUTH_MODE: "none" }),
    ).toThrow(/Invalid frontend environment/);
  });
});
