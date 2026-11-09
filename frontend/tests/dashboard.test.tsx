import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse, delay } from "msw";
import { describe, expect, it } from "vitest";

import { Dashboard } from "@/app/components/Dashboard";
import { HealthIndicators } from "@/app/components/HealthIndicators";
import { VaultStatistics } from "@/app/components/VaultStatistics";

import { API, apiError, metrics, server } from "./msw";
import { renderWithApp } from "./render";

describe("Dashboard", () => {
  it("shows skeletons, then metric cards", async () => {
    server.use(
      http.get(`${API}/api/vault/metrics`, async () => {
        await delay(50);
        return HttpResponse.json(metrics);
      }),
    );
    renderWithApp(<Dashboard />);
    expect(screen.getAllByTestId("metric-skeleton")).toHaveLength(2);

    const reviews = await screen.findByRole("region", { name: "Pending reviews" });
    expect(within(reviews).getByText("2")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Documents" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Pinecone vectors" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Documents by category" })).not.toBeInTheDocument();
    expect(screen.getByText("1 within 7 days")).toBeInTheDocument();
    expect(screen.queryByTestId("metric-skeleton")).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Custom categories" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Bulk upload" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Service health" })).not.toBeInTheDocument();
  });

  it("marks expirations within 7 days as urgent (red)", async () => {
    renderWithApp(<Dashboard />);
    const list = await screen.findByRole("region", { name: "Upcoming expirations" });
    const items = await within(list).findAllByRole("listitem");
    expect(items[0]).toHaveAttribute("data-urgent", "true");
    expect(items[0].className).toContain("bg-[#fee2e2]");
    expect(within(items[0]).getByText("in 3 days")).toBeInTheDocument();
    expect(items[1]).toHaveAttribute("data-urgent", "false");
  });

  it("shows an error with retry", async () => {
    let calls = 0;
    server.use(
      http.get(`${API}/api/vault/metrics`, async () => {
        calls += 1;
        return calls === 1
          ? apiError(503, "service_unavailable", "down")
          : HttpResponse.json(metrics);
      }),
    );
    renderWithApp(<Dashboard />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Waking up the vault");
    await userEvent.click(within(alert).getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("region", { name: "Pending reviews" })).toBeInTheDocument();
  });

  it("shows year expirations only from day 30 through day 364", async () => {
    server.use(http.get(`${API}/api/vault/metrics`, () => HttpResponse.json({
      ...metrics,
      year_expirations: [29, 30, 364, 365].map((days) => ({
        ...metrics.upcoming_expirations[0], document_id: `d-${days}`,
        document_name: `Expires ${days}`, days_remaining: days,
      })),
    })));
    renderWithApp(<Dashboard />);
    const section = screen.getByRole("region", { name: "Expiring in the next 1 year" });
    expect(await within(section).findByText("Expires 30")).toBeInTheDocument();
    expect(within(section).getByText("Expires 364")).toBeInTheDocument();
    expect(within(section).queryByText("Expires 29")).not.toBeInTheDocument();
    expect(within(section).queryByText("Expires 365")).not.toBeInTheDocument();
  });
});

it("shows moved document and vector statistics on service health", async () => {
  renderWithApp(<VaultStatistics />);
  const documents = await screen.findByRole("region", { name: "Documents" });
  expect(within(documents).getByText("12")).toBeInTheDocument();
  expect(within(screen.getByRole("region", { name: "Pinecone vectors" })).getByText("31")).toBeInTheDocument();
  expect(screen.getByRole("region", { name: "Documents by category" })).toHaveTextContent("medical");
});
describe("HealthIndicators", () => {
  it("polls and updates from down to operational", async () => {
    let healthy = false;
    server.use(
      http.get(`${API}/readyz`, () =>
        healthy
          ? HttpResponse.json({ status: "ok", checks: { postgres: "ok", redis: "ok", pinecone: "ok" } })
          : HttpResponse.json({ status: "degraded", checks: { postgres: "ok", redis: "error", pinecone: "ok" } }),
      ),
    );
    renderWithApp(<HealthIndicators pollMs={40} />);
    await waitFor(() => expect(screen.getByTestId("health-postgres")).toHaveAttribute("data-state", "ok"));
    expect(screen.getByTestId("health-redis")).toHaveAttribute("data-state", "error");

    healthy = true;
    await waitFor(() => expect(screen.getByTestId("health-redis")).toHaveAttribute("data-state", "ok"));
  });

  it("shows all red when the API is unreachable", async () => {
    server.use(http.get(`${API}/readyz`, () => HttpResponse.error()));
    renderWithApp(<HealthIndicators pollMs={10_000} />);
    expect(await screen.findByText(/API unreachable/)).toBeInTheDocument();
    expect(screen.getByTestId("health-pinecone")).toHaveAttribute("data-state", "error");
  });
});
