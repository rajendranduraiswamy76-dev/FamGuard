import { z } from "zod";

const publicEnvSchema = z
  .object({
    NEXT_PUBLIC_API_BASE_URL: z
      .string({ error: "NEXT_PUBLIC_API_BASE_URL is required" })
      .url("NEXT_PUBLIC_API_BASE_URL must be an absolute URL")
      .transform((url) => url.replace(/\/+$/, "")),
    NEXT_PUBLIC_AUTH_MODE: z.enum(["clerk", "test"]).default("clerk"),
    NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: z.string().optional(),
  })
  .superRefine((env, ctx) => {
    if (env.NEXT_PUBLIC_AUTH_MODE === "clerk" && !env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY) {
      ctx.addIssue({
        code: "custom",
        path: ["NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY"],
        message: "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY is required when NEXT_PUBLIC_AUTH_MODE=clerk",
      });
    }
  });

export type PublicEnv = z.infer<typeof publicEnvSchema>;

export function parsePublicEnv(raw: Record<string, string | undefined>): PublicEnv {
  const result = publicEnvSchema.safeParse(raw);
  if (!result.success) {
    const details = result.error.issues.map((i) => `- ${i.message}`).join("\n");
    throw new Error(`Invalid frontend environment:\n${details}`);
  }
  return result.data;
}

// Each variable must be referenced literally so Next.js can inline it into the client bundle.
export const env: PublicEnv = parsePublicEnv({
  NEXT_PUBLIC_API_BASE_URL: process.env.NEXT_PUBLIC_API_BASE_URL,
  NEXT_PUBLIC_AUTH_MODE: process.env.NEXT_PUBLIC_AUTH_MODE,
  NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY,
});

export const isTestAuth = env.NEXT_PUBLIC_AUTH_MODE === "test";
