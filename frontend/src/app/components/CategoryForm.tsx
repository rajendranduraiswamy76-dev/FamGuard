"use client";

import { useState, type FormEvent } from "react";

import { ApiError } from "@/lib/api";
import { useApi } from "@/lib/auth";
import { errorMessage } from "@/lib/hooks";
import type { Category } from "@/lib/types";
import { slugError, toSlug } from "@/lib/validation";

export function CategoryForm({
  categories,
  isAdmin,
  onChange,
}: {
  categories: Category[];
  isAdmin: boolean;
  onChange: (updater: (current: Category[]) => Category[]) => void;
}) {
  const api = useApi();
  const [displayName, setDisplayName] = useState("");
  const [manualSlug, setManualSlug] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState<{ kind: "error" | "success"; text: string } | null>(null);

  const slug = manualSlug ?? toSlug(displayName);
  const validation = displayName.trim() ? slugError(slug) : null;
  const canSubmit = !!displayName.trim() && !validation && !submitting;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!canSubmit) return;
    const optimistic: Category = {
      id: `pending-${slug}`,
      category_slug: slug,
      category_display_name: displayName.trim(),
      is_baseline: false,
    };
    setSubmitting(true);
    setMessage(null);
    onChange((list) => [...list, optimistic]);
    try {
      const created = await api.createCategory(slug, displayName.trim());
      onChange((list) => list.map((c) => (c.id === optimistic.id ? created : c)));
      setMessage({ kind: "success", text: `Added “${created.category_display_name}”.` });
      setDisplayName("");
      setManualSlug(null);
    } catch (error) {
      onChange((list) => list.filter((c) => c.id !== optimistic.id));
      const text =
        error instanceof ApiError && error.status === 409
          ? `A category with the slug “${slug}” already exists.`
          : errorMessage(error);
      setMessage({ kind: "error", text });
    } finally {
      setSubmitting(false);
    }
  }

  const custom = categories.filter((c) => !c.is_baseline);

  return (
    <section aria-label="Custom categories" className="enterprise-panel">
      <h2 className="section-title">Categories</h2>
      <ul className="mt-2 flex flex-wrap gap-1.5" aria-label="Available categories">
        {categories.map((c) => (
          <li
            key={c.id}
            className={`rounded-full px-2.5 py-0.5 text-xs ${
              c.is_baseline ? "bg-slate-100 text-slate-600" : "bg-indigo-50 text-indigo-700"
            }`}
          >
            {c.category_slug}
          </li>
        ))}
      </ul>

      {!isAdmin ? (
        <p className="mt-3 text-xs text-slate-500">Only family admins can add custom categories.</p>
      ) : (
        <form onSubmit={submit} className="mt-5 grid items-start gap-4 sm:grid-cols-2" noValidate>
          <div>
            <label htmlFor="category-name" className="block text-xs font-medium text-slate-600">
              Category display name
            </label>
            <input
              id="category-name"
              value={displayName}
              maxLength={80}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="e.g. Vehicle Docs"
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm focus:border-indigo-500 focus:outline-none"
            />
          </div>
          <div>
            <label htmlFor="category-slug" className="block text-xs font-medium text-slate-600">
              Slug
            </label>
            <input
              id="category-slug"
              value={slug}
              onChange={(e) => setManualSlug(e.target.value)}
              aria-invalid={!!validation}
              aria-describedby="category-slug-help"
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-1.5 font-mono text-sm focus:border-indigo-500 focus:outline-none"
            />
            <p
              id="category-slug-help"
              className={`mt-1 text-xs ${validation ? "text-red-600" : "text-slate-500"}`}
            >
              {validation ?? `Stored in Pinecone metadata as {"category": "${slug || "…"}"}`}
            </p>
          </div>
          <div className="action-bar sm:col-span-2">
          <button
            type="submit"
            disabled={!canSubmit}
            className="rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
          >
            {submitting ? "Adding…" : "Add category"}
          </button>
          </div>
          {message ? (
            <p role="status" className={`text-xs sm:col-span-2 ${message.kind === "error" ? "text-red-600" : "text-emerald-700"}`}>
              {message.text}
            </p>
          ) : null}
        </form>
      )}
      {isAdmin && custom.length === 0 ? (
        <p className="mt-2 text-xs text-slate-500">No custom categories yet.</p>
      ) : null}
    </section>
  );
}
