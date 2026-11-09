// Must match backend CATEGORY_SLUG_PATTERN and the 48-character limit.
export const CATEGORY_SLUG_PATTERN = /^[a-z0-9]+(-[a-z0-9]+)*$/;
export const MAX_SLUG_LENGTH = 48;

export function toSlug(displayName: string): string {
  return displayName
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, MAX_SLUG_LENGTH)
    .replace(/-+$/g, "");
}

export function slugError(slug: string): string | null {
  if (!slug) return "Slug is required";
  if (slug.length > MAX_SLUG_LENGTH) return `Slug must be at most ${MAX_SLUG_LENGTH} characters`;
  if (!CATEGORY_SLUG_PATTERN.test(slug)) {
    return "Use lowercase letters and numbers separated by single hyphens";
  }
  return null;
}

export function isIsoDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const date = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(date.getTime()) && date.toISOString().startsWith(value);
}
