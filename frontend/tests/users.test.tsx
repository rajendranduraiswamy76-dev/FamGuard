import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import { AppShell } from "@/app/components/AppShell";
import { UsersView } from "@/app/components/UsersView";
import { StaticTokenProvider } from "@/lib/auth";

import { API, apiError, me, server } from "./msw";
import { renderWithApp } from "./render";

describe("Users", () => {
  it("loads the roster and protects the current admin", async () => {
    renderWithApp(<UsersView />);
    expect(await screen.findByRole("table", { name: "Family users" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete Ann" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Edit Ann" }));
    expect(screen.getByLabelText("Role")).toBeDisabled();
    expect(await screen.findByRole("region", { name: "Custom categories" })).toBeInTheDocument();
  });

  it("creates a dependent and updates the shared roster", async () => {
    let body: unknown;
    server.use(http.post(`${API}/api/members`, async ({ request }) => {
      body = await request.json();
      return HttpResponse.json({
        ...me.members[1], id: "m-child", display_name: "Child",
      }, { status: 201 });
    }));
    renderWithApp(<UsersView />);
    await userEvent.click(await screen.findByRole("button", { name: "New user" }));
    await userEvent.type(screen.getByLabelText("Display name"), " Child ");
    await userEvent.selectOptions(screen.getByLabelText("Relationship"), "child");
    await userEvent.click(screen.getByRole("button", { name: "Save user" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Child created.");
    expect(body).toEqual({ display_name: "Child", email: null, role: "dependent", relationship: "child" });
    expect(screen.getByRole("button", { name: "Edit Child" })).toBeInTheDocument();
  });

  it("requires email for a login role", async () => {
    renderWithApp(<UsersView />);
    await userEvent.click(await screen.findByRole("button", { name: "New user" }));
    await userEvent.selectOptions(screen.getByLabelText("Role"), "member");
    await userEvent.selectOptions(screen.getByLabelText("Relationship"), "spouse");
    expect(screen.getByLabelText("Email")).toBeRequired();
    await userEvent.selectOptions(screen.getByLabelText("Role"), "dependent");
    expect(screen.getByLabelText("Email")).not.toBeRequired();
  });

  it("edits name, email and role using PUT", async () => {
    let body: unknown;
    server.use(http.put(`${API}/api/members/m-john`, async ({ request }) => {
      body = await request.json();
      return HttpResponse.json({
        ...me.members[1], display_name: "Jonathan", email: "john@example.com", role: "member",
        relationship: "spouse",
      });
    }));
    renderWithApp(<UsersView />);
    await userEvent.click(await screen.findByRole("button", { name: "Edit John" }));
    await userEvent.clear(screen.getByLabelText("Display name"));
    await userEvent.type(screen.getByLabelText("Display name"), "Jonathan");
    await userEvent.type(screen.getByLabelText("Email"), "john@example.com");
    await userEvent.selectOptions(screen.getByLabelText("Role"), "member");
    await userEvent.selectOptions(screen.getByLabelText("Relationship"), "spouse");
    await userEvent.click(screen.getByRole("button", { name: "Save user" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Jonathan updated.");
    expect(body).toEqual({ display_name: "Jonathan", email: "john@example.com", role: "member", relationship: "spouse" });
    expect(screen.getByText("spouse")).toBeInTheDocument();
    expect(screen.getByText("Awaiting sign-in")).toBeInTheDocument();
  });

  it("keeps the form and original roster on an update conflict", async () => {
    server.use(http.put(`${API}/api/members/m-john`, () =>
      apiError(409, "conflict", "Member name already exists in this family")));
    renderWithApp(<UsersView />);
    await userEvent.click(await screen.findByRole("button", { name: "Edit John" }));
    await userEvent.click(screen.getByRole("button", { name: "Save user" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Member name already exists");
    expect(screen.getByRole("region", { name: "Edit user" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit John" })).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("requires confirmation and handles a bodyless 204 delete", async () => {
    const remove = vi.fn(() => new HttpResponse(null, { status: 204 }));
    server.use(http.delete(`${API}/api/members/m-john`, remove));
    renderWithApp(<UsersView />);
    await userEvent.click(await screen.findByRole("button", { name: "Delete John" }));
    expect(remove).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(remove).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Delete John" }));
    await userEvent.click(screen.getByRole("button", { name: "Confirm delete" }));
    expect(await screen.findByRole("status")).toHaveTextContent("John deleted.");
    expect(screen.queryByRole("button", { name: "Edit John" })).not.toBeInTheDocument();
  });

  it("shows blocked deletion without removing the member", async () => {
    server.use(http.delete(`${API}/api/members/m-john`, () =>
      apiError(409, "conflict", "Cannot delete a member who owns documents or review records")));
    renderWithApp(<UsersView />);
    await userEvent.click(await screen.findByRole("button", { name: "Delete John" }));
    await userEvent.click(screen.getByRole("button", { name: "Confirm delete" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("owns documents or review records");
    expect(screen.getByRole("button", { name: "Edit John" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Confirm deletion" })).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("blocks non-admins and hides the Users navigation link", async () => {
    server.use(http.get(`${API}/api/me`, () => HttpResponse.json({
      ...me, member: { ...me.member, role: "member" },
    })));
    render(<StaticTokenProvider><AppShell userSlot={null}><UsersView /></AppShell></StaticTokenProvider>);
    expect(await screen.findByRole("alert")).toHaveTextContent("Only family admins");
    expect(screen.queryByRole("link", { name: "Users & Categories management" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "New user" })).not.toBeInTheDocument();
  });

  it("shows the admin Users navigation link", async () => {
    render(<StaticTokenProvider><AppShell userSlot={null}><UsersView /></AppShell></StaticTokenProvider>);
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(await within(nav).findByRole("link", { name: "Users & Categories management" })).toHaveAttribute("href", "/users");
  });

  it("reports roster load failures with a retry", async () => {
    let calls = 0;
    server.use(http.get(`${API}/api/me`, () => ++calls === 1
      ? apiError(503, "service_unavailable", "down") : HttpResponse.json(me)));
    renderWithApp(<UsersView />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Waking up the vault");
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("table", { name: "Family users" })).toBeInTheDocument();
  });
});
