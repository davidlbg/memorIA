import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { Api } from "../services/api";
import { ApiUsage, usd } from "./ApiUsage";

it("shows the shared baseline separately from new spending and uncertain reservations", async () => {
  const api = new Api({ url: "http://localhost:8000", token: "synthetic" });
  vi.spyOn(api, "usageStatus").mockResolvedValue({
    enabled: true,
    month: "2026-11",
    limit_usd: "50.000000",
    prior_usd: "7.470000",
    recorded_usd: "0.002910",
    reserved_usd: "5.340000",
    available_usd: "37.187090",
    query_reservation_usd: "5.340000",
    baseline_known: true,
    started_at: null,
    calls: 2,
    unconfirmed_calls: 1,
    by_purpose: { queries: "0.002910" },
    pricing_version: "test",
  });
  render(<ApiUsage api={api} connected />);
  const summary = await screen.findByText(/Consumo da API/);
  expect(summary).toHaveTextContent(/7,47/);
  await userEvent.click(summary);
  expect(screen.getByText(/1 chamada\(s\) sem confirmação/)).toBeVisible();
  expect(screen.getByText(/Reservas não são cobranças/)).toBeVisible();
  expect(screen.getByText(/Uso desta chave fora deste backend/)).toBeVisible();
  expect(usd("0.000001", 4)).toContain("< US$");
});

it("does not show a stale balance as current when accounting cannot be read", async () => {
  const api = new Api({ url: "http://localhost:8000", token: "synthetic" });
  vi.spyOn(api, "usageStatus").mockRejectedValue(new Error("offline"));
  render(<ApiUsage api={api} connected />);
  expect(
    await screen.findByText(/valores não estão disponíveis/),
  ).toBeVisible();
  expect(screen.queryByText(/Disponível estimado/)).toBeNull();
});
