import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import type { ConnectorStatus } from "@whatsapp-memory/shared-types/whatsapp";
import { Api } from "../services/api";
import { WhatsAppConnection } from "./WhatsAppConnection";

function status(
  state: ConnectorStatus["state"] = "DISCONNECTED",
): ConnectorStatus {
  return {
    available: true,
    network_enabled: true,
    state,
    account_id: null,
    qr_data_url: null,
    qr_expires_at: null,
    last_status_at: new Date().toISOString(),
    metrics: {
      received: 0,
      discarded: 0,
      duplicates: 0,
      failures: 0,
      reconnections: 0,
      pending: 0,
      connected_seconds: 0,
      last_event_at: null,
      last_synced_at: null,
      delivery_latency_ms: 0,
      gap_detected: false,
    },
    history_notice: "Histórico parcial: somente eventos entregues.",
    error_code: null,
  };
}
describe("WhatsApp connector panel", () => {
  it("shows configuration and unavailable states without fabricated connection data", async () => {
    const api = new Api({ url: "http://localhost:8000", token: "test" });
    vi.spyOn(api, "whatsappStatus").mockRejectedValue(
      new Error("Conector não configurado"),
    );
    render(<WhatsAppConnection api={api} enabled />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Conector não configurado",
    );
    expect(
      screen.getByRole("button", { name: "Conectar WhatsApp" }),
    ).toBeDisabled();
  });
  it("connects, renders an expiring QR and confirms disconnect", async () => {
    const api = new Api({ url: "http://localhost:8000", token: "test" }),
      user = userEvent.setup();
    vi.spyOn(api, "whatsappStatus").mockResolvedValue(status());
    vi.spyOn(api, "whatsappConnect").mockResolvedValue({
      ...status("QR_REQUIRED"),
      qr_data_url: "data:image/png;base64,AAAA",
      qr_expires_at: new Date(Date.now() + 60000).toISOString(),
    });
    vi.spyOn(api, "whatsappDisconnect").mockResolvedValue(status());
    render(<WhatsAppConnection api={api} enabled />);
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Conectar WhatsApp" }),
      ).toBeEnabled(),
    );
    await user.click(screen.getByRole("button", { name: "Conectar WhatsApp" }));
    expect(
      await screen.findByAltText("QR Code temporário para vincular o WhatsApp"),
    ).toBeVisible();
    await user.click(
      screen.getByRole("button", { name: "Desconectar e excluir credenciais" }),
    );
    expect(api.whatsappDisconnect).not.toHaveBeenCalled();
    await user.click(
      screen.getByRole("button", { name: "Confirmar desconexão" }),
    );
    await waitFor(() => expect(api.whatsappDisconnect).toHaveBeenCalledOnce());
  });
  it("does not display an expired QR and displays capture gaps", async () => {
    const api = new Api({ url: "http://localhost:8000", token: "test" });
    const current = {
      ...status("QR_REQUIRED"),
      qr_data_url: "data:image/png;base64,AAAA",
      qr_expires_at: new Date(Date.now() - 1000).toISOString(),
    };
    current.metrics.gap_detected = true;
    vi.spyOn(api, "whatsappStatus").mockResolvedValue(current);
    render(<WhatsAppConnection api={api} enabled />);
    expect(
      await screen.findByText(
        "Aguardando um novo QR Code válido. O código anterior expirou.",
      ),
    ).toBeVisible();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(screen.getByText(/Houve uma interrupção/)).toBeVisible();
  });
});
