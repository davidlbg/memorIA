import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Health } from "@whatsapp-memory/shared-types";
import { Api } from "../services/api";
import { OpenAISettings } from "./OpenAISettings";

const health: Health = {
  status: "ok",
  llm_provider: "mock",
  embedding_provider: "mock",
  embedding_identity: "mock:hash-v1:1024",
  external_ai_enabled: false,
  import_max_bytes: 5000000,
  openai_available: false,
  openai_setup_available: true,
};
const key = "sk-synthetic-only-not-a-real-key";

describe("OpenAI credentials", () => {
  it("requires explicit consent, sends the key once and clears the input without browser persistence", async () => {
    const api = new Api({ url: "http://localhost:8000", token: "synthetic" });
    const configured = vi
      .spyOn(api, "configureOpenAI")
      .mockResolvedValue({ ...health, openai_available: true });
    const changed = vi.fn();
    const user = userEvent.setup();
    render(
      <OpenAISettings
        api={api}
        health={health}
        connected
        onChanged={changed}
      />,
    );
    const input = screen.getByLabelText("Chave de API");
    expect(input).toHaveAttribute("type", "password");
    await user.type(input, key);
    const button = screen.getByRole("button", {
      name: "Verificar chave e ativar OpenAI",
    });
    expect(button).toBeDisabled();
    await user.click(screen.getByRole("checkbox"));
    await user.click(button);
    await waitFor(() => expect(changed).toHaveBeenCalledOnce());
    expect(configured).toHaveBeenCalledExactlyOnceWith(key);
    expect(input).toHaveValue("");
    expect(JSON.stringify(localStorage)).not.toContain(key);
    expect(JSON.stringify(sessionStorage)).not.toContain(key);
    expect(screen.getByRole("status")).not.toHaveTextContent(key);
  });
  it("clears a failed credential and supports explicit disabling", async () => {
    const api = new Api({ url: "http://localhost:8000", token: "synthetic" });
    vi.spyOn(api, "configureOpenAI").mockRejectedValue(
      new Error("OpenAI: chave inválida"),
    );
    const removed = vi.spyOn(api, "removeOpenAI").mockResolvedValue(health);
    const changed = vi.fn();
    const user = userEvent.setup();
    render(
      <OpenAISettings
        api={api}
        health={{ ...health, openai_available: true }}
        connected
        onChanged={changed}
      />,
    );
    await user.type(screen.getByLabelText("Chave de API"), key);
    await user.click(screen.getByRole("checkbox"));
    await user.click(
      screen.getByRole("button", { name: "Verificar chave e ativar OpenAI" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "chave inválida",
    );
    expect(screen.getByLabelText("Chave de API")).toHaveValue("");
    await user.click(
      screen.getByRole("button", { name: "Remover chave e desativar OpenAI" }),
    );
    await waitFor(() => expect(removed).toHaveBeenCalledOnce());
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Novos envios à OpenAI desativados",
    );
  });
});
