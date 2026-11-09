import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { Assistant } from "./Assistant";
import { Api } from "../services/api";

describe("side panel", () => {
  it("renders an honest empty state and navigates to import/settings/memories", async () => {
    const user = userEvent.setup();
    render(<App />);
    expect(
      screen.getByRole("heading", { name: /Relembre a conversa/ }),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Importar" }));
    expect(screen.getByText("Traga uma conversa.")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Conferir prévia" }),
    ).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Memórias" }));
    expect(screen.getByText("Nenhuma memória por aqui.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Ajustes" }));
    expect(screen.getByLabelText("Token de acesso local")).toHaveAttribute(
      "type",
      "password",
    );
  });
  it("submits a question, shows loading, and renders original evidence", async () => {
    const api = new Api({ url: "http://localhost:8000", token: "test" });
    vi.spyOn(api, "validateSources").mockResolvedValue([
      { id: "11111111-1111-4111-8111-111111111111", revision: 1 },
    ]);
    let resolveAnswer: (
      value: Awaited<ReturnType<Api["query"]>>,
    ) => void = () => {
      throw new Error("Pending request not initialized");
    };
    vi.spyOn(api, "query").mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveAnswer = resolve;
        }),
    );
    const user = userEvent.setup();
    render(
      <Assistant
        api={api}
        connected
        conversationId=""
        contactId=""
        contacts={[]}
        onContact={() => undefined}
        onImport={() => undefined}
      />,
    );
    await user.type(
      screen.getByLabelText("Pergunte ao seu histórico"),
      "documento",
    );
    await user.click(screen.getByRole("button", { name: "Enviar pergunta" }));
    expect(screen.getByRole("status")).toHaveTextContent(
      "Pesquisando nas conversas",
    );
    expect(
      screen.getByRole("button", { name: "Enviar pergunta" }),
    ).toBeDisabled();
    resolveAnswer({
      claims: [
        {
          kind: "explicit_statement",
          text: "Eu prometo enviar.",
          quote: "Eu prometo enviar.",
          source_message_ids: ["11111111-1111-4111-8111-111111111111"],
        },
      ],
      insufficient_evidence: false,
      provider: "mock",
      sources: [
        {
          id: "11111111-1111-4111-8111-111111111111",
          conversation_id: "22222222-2222-4222-8222-222222222222",
          sender_contact_id: null,
          sender_name_snapshot: "Alice",
          content: "Eu prometo enviar.",
          message_type: "text",
          sent_at: "2026-10-31T10:00:00Z",
        },
      ],
    });
    await waitFor(() =>
      expect(screen.getByText("Eu prometo enviar.")).toBeInTheDocument(),
    );
    expect(screen.queryByRole("blockquote")).not.toBeInTheDocument();
    await user.click(screen.getByText("1 mensagem original"));
    await user.click(screen.getByText("Alice"));
    await user.click(screen.getByText("Detalhes da fonte"));
    expect(screen.getByText(/Mensagem 11111111/)).toBeInTheDocument();
    await user.click(screen.getByLabelText("Opções do chat"));
    await user.click(screen.getByRole("button", { name: "Limpar conversa" }));
    expect(screen.queryByText("Eu prometo enviar.")).not.toBeInTheDocument();
  });
  it("shows query errors and insufficient evidence explicitly", async () => {
    const api = new Api({ url: "http://localhost:8000", token: "test" });
    vi.spyOn(api, "query")
      .mockRejectedValueOnce(new Error("IA indisponível"))
      .mockResolvedValueOnce({
        claims: [],
        sources: [],
        insufficient_evidence: true,
        provider: "mock",
      });
    const user = userEvent.setup();
    render(
      <Assistant
        api={api}
        connected
        conversationId=""
        contactId=""
        contacts={[]}
        onContact={() => undefined}
        onImport={() => undefined}
      />,
    );
    await user.type(
      screen.getByLabelText("Pergunte ao seu histórico"),
      "teste",
    );
    await user.click(screen.getByRole("button", { name: "Enviar pergunta" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "IA indisponível",
    );
    await user.type(
      screen.getByLabelText("Pergunte ao seu histórico"),
      "outra pergunta",
    );
    await user.click(screen.getByRole("button", { name: "Enviar pergunta" }));
    expect(
      await screen.findByText(
        "O histórico disponível não estabelece uma resposta para essa pergunta.",
      ),
    ).toBeInTheDocument();
  });
});
