"use client";

import { useState, type FormEvent } from "react";

import { useApi } from "@/lib/auth";
import { errorMessage, useAsync } from "@/lib/hooks";
import type { Member, MemberInput, Role } from "@/lib/types";
import { useVault } from "@/lib/vault-context";
import { CategoryForm } from "./CategoryForm";

const inputClass = "w-full rounded-md border border-slate-300 px-3 py-2 text-sm";
const buttonClass = "rounded-md border border-slate-300 px-3 py-2 text-sm font-medium disabled:opacity-50";

export function UsersView() {
  const api = useApi();
  const vault = useVault();
  const categories = useAsync(() => api.categories(), [api]);
  const [editing, setEditing] = useState<Member | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("dependent");
  const [relationship, setRelationship] = useState<NonNullable<Member["relationship"]> | "">("");
  const [deleting, setDeleting] = useState<Member | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  function openForm(member: Member | null) {
    setEditing(member);
    setName(member?.display_name ?? "");
    setEmail(member?.email ?? "");
    setRole(member?.role ?? "dependent");
    setRelationship(member?.relationship ?? "");
    setFormOpen(true);
    setDeleting(null);
    setError(null);
    setNotice(null);
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const displayName = name.trim();
    if (!displayName || displayName.length > 80 || [...displayName].some((c) => c.charCodeAt(0) < 32)) {
      setError("Enter a name of 1 to 80 characters without control characters.");
      return;
    }
    setBusy(true);
    setError(null);
    setNotice(null);
    const body: MemberInput = {
      display_name: displayName, email: email.trim() || null, role,
      relationship: relationship || null,
    };
    try {
      const member = editing
        ? await api.updateMember(editing.id, body)
        : await api.createMember(body);
      vault.setData((current) => current && {
        ...current,
        member: current.member.id === member.id ? member : current.member,
        members: [...current.members.filter((m) => m.id !== member.id), member]
          .sort((a, b) => a.display_name.localeCompare(b.display_name)),
      });
      setNotice(`${member.display_name} ${editing ? "updated" : "created"}.`);
      setFormOpen(false);
      setEditing(null);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!deleting) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await api.deleteMember(deleting.id);
      vault.setData((current) => current && {
        ...current, members: current.members.filter((m) => m.id !== deleting.id),
      });
      setNotice(`${deleting.display_name} deleted.`);
      setDeleting(null);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  if (vault.error) {
    return (
      <div role="alert" className="rounded-xl border border-red-200 bg-red-50 p-4 text-red-700">
        <p>{errorMessage(vault.error)}</p>
        <button type="button" onClick={vault.reload} className="mt-2 underline">Retry</button>
      </div>
    );
  }
  if (!vault.data) return <p role="status">Loading family users...</p>;
  if (vault.data.member.role !== "admin") {
    return <p role="alert">Only family admins can manage users.</p>;
  }

  return (
    <div className="space-y-6">
      <div className="page-heading">
        <div>
          <h1>Users &amp; Categories management</h1>
          <p>
            Manage family members and dependents for {vault.data.family.name}.
          </p>
        </div>
        <button type="button" disabled={busy} onClick={() => openForm(null)}
          className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50">
          New user
        </button>
      </div>
      <p className="text-sm text-slate-600">
        Admins manage the whole family vault. Members access their own documents.
        Dependents have no login. Adding an admin or member links their matching email on first sign-in;
        it does not send an invitation or create a Clerk account.
      </p>
      {error && <p role="alert" className="rounded-md bg-red-50 p-3 text-sm text-red-700">{error}</p>}
      {notice && <p role="status" className="rounded-md bg-emerald-50 p-3 text-sm text-emerald-700">{notice}</p>}

      {formOpen && (
        <section aria-label={editing ? "Edit user" : "New user"} className="enterprise-panel">
          <h2 className="mb-4 font-semibold">{editing ? `Edit ${editing.display_name}` : "New user"}</h2>
          <form onSubmit={save} className="space-y-4">
            <fieldset disabled={busy} className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <label className="space-y-1 text-sm">
                <span>Display name</span>
                <input className={inputClass} value={name} onChange={(e) => setName(e.target.value)}
                  required maxLength={80} autoComplete="name" />
              </label>
              <label className="space-y-1 text-sm">
                <span>Email</span>
                <input className={inputClass} type="email" value={email} onChange={(e) => setEmail(e.target.value)}
                  required={role !== "dependent"} autoComplete="email" />
              </label>
              <label className="space-y-1 text-sm">
                <span>Role</span>
                <select className={inputClass} value={role} onChange={(e) => {
                  const value = e.target.value;
                  if (value === "admin" || value === "member" || value === "dependent") setRole(value);
                }} disabled={editing?.id === vault.data.member.id}>
                  <option value="dependent" disabled={editing?.has_login}>Dependent</option>
                  <option value="member">Member</option>
                  <option value="admin">Admin</option>
                </select>
              </label>
              <div className="space-y-1 text-sm">
                <label htmlFor="member-relationship">Relationship</label>
                <select id="member-relationship" className={inputClass} value={relationship} onChange={(event) => {
                  const value = event.target.value;
                  if (value === "" || value === "spouse" || value === "child" || value === "others") {
                    setRelationship(value);
                  }
                }}>
                  <option value="">Not specified</option>
                  <option value="spouse">Spouse</option>
                  <option value="child">Child</option>
                  <option value="others">Others</option>
                </select>
              </div>
            </fieldset>
            <p className="text-xs text-slate-500">Relationships are family labels; Role controls access permissions.</p>
            {editing?.has_login && <p className="text-xs text-slate-500">
              This user has a linked Clerk login. Changing this contact email does not change their
              Clerk account or unlink their login.
            </p>}
            <div className="action-bar">
              <button type="submit" disabled={busy}
                className="rounded-md bg-indigo-600 px-3 py-2 text-sm font-medium text-white disabled:opacity-50">
                {busy ? "Saving..." : "Save user"}
              </button>
              <button type="button" disabled={busy} className={buttonClass}
                onClick={() => { setFormOpen(false); setError(null); }}>Cancel</button>
            </div>
          </form>
        </section>
      )}

      {deleting && (
        <section aria-label="Confirm deletion" className="rounded-xl border border-red-200 bg-red-50 p-4">
          <h2 className="font-semibold">Delete {deleting.display_name}?</h2>
          <p className="mt-2 text-sm">
            This removes their family membership, not their Clerk account. Deletion is blocked if
            they own documents or review records. If they sign in again, they may create a new family vault.
          </p>
          <div className="mt-3 flex gap-2">
            <button type="button" disabled={busy} onClick={() => void remove()}
              className="rounded-md bg-red-600 px-3 py-2 text-sm font-medium text-white disabled:opacity-50">
              {busy ? "Deleting..." : "Confirm delete"}
            </button>
            <button type="button" disabled={busy} className={buttonClass}
              onClick={() => { setDeleting(null); setError(null); }}>Cancel</button>
          </div>
        </section>
      )}

      <div tabIndex={0} role="region" aria-label="Family users table" className="table-scroll bg-panel">
        <table className="enterprise-table min-w-[800px]">
          <caption className="sr-only">Family users</caption>
          <thead>
            <tr>
              {["Name", "Email", "Role", "Relationship", "Login", "Actions"].map((heading) => (
                <th key={heading} scope="col" className="px-4 py-3">{heading}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {vault.data.members.map((member) => (
              <tr key={member.id} className="border-t border-slate-100">
                <th scope="row" className="px-4 py-3 font-medium">
                  {member.display_name}{member.id === vault.data?.member.id ? " (you)" : ""}
                </th>
                <td className="px-4 py-3">{member.email ?? "Not set"}</td>
                <td className="px-4 py-3 capitalize">{member.role}</td>
                <td className="px-4 py-3 capitalize">{member.relationship ?? "Not specified"}</td>
                <td className="px-4 py-3">
                  {member.has_login ? "Linked" : member.role === "dependent" ? "No login" : "Awaiting sign-in"}
                </td>
                <td className="px-4 py-3">
                  <div className="flex gap-2">
                    <button type="button" disabled={busy} className={buttonClass}
                      aria-label={`Edit ${member.display_name}`} onClick={() => openForm(member)}>Edit</button>
                    <button type="button" disabled={busy || member.id === vault.data?.member.id}
                      aria-label={`Delete ${member.display_name}`} className={`${buttonClass} text-red-700`}
                      onClick={() => {
                        setDeleting(member); setFormOpen(false); setError(null); setNotice(null);
                      }}>Delete</button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-slate-500">
        You cannot delete yourself or change your own admin role, ensuring the family keeps an admin.
      </p>
      {categories.error ? (
        <div role="alert" className="rounded-lg border border-red-200 bg-red-50 p-4 text-red-700">
          <p>{errorMessage(categories.error)}</p>
          <button type="button" onClick={categories.reload} className="mt-2 underline">Retry categories</button>
        </div>
      ) : categories.loading ? <p role="status">Loading categories...</p> : (
        <CategoryForm categories={categories.data ?? []} isAdmin
          onChange={(update) => categories.setData((current) => update(current ?? []))} />
      )}
    </div>
  );
}
