import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { ConversationExplorer } from "./ConversationExplorer";
import { Api } from "../services/api";
import type { Source } from "@whatsapp-memory/shared-types";

const conversation = {
  id: "22222222-2222-4222-8222-222222222222",
  title: "Synthetic",
  conversation_type: "direct",
  imported_at: "2026-11-03T10:00:00Z",
};
const source: Source = {
  id: "11111111-1111-4111-8111-111111111111",
  conversation_id: conversation.id,
  sender_contact_id: null,
  sender_name_snapshot: "Alice",
  content: "Mensagem sintética sobre o prazo.",
  message_type: "text",
  sent_at: "2026-11-03T10:00:00Z",
};

describe("conversation exploration", () => {
  it("replaces revoked originals when the panel regains focus", async () => {
    const api = new Api({ url: "http://localhost:8000", token: "synthetic" });
    vi.spyOn(api, "conversation")
      .mockResolvedValueOnce({ conversation, messages: [source] })
      .mockResolvedValue({ conversation, messages: [] });
    vi.spyOn(api, "validateSources").mockResolvedValue([]);
    render(
      <ConversationExplorer
        api={api}
        connected
        conversationId={conversation.id}
        onChanged={() => undefined}
      />,
    );
    await screen.findByText(source.content);
    await act(async () => {
      window.dispatchEvent(new Event("focus"));
    });
    await waitFor(() =>
      expect(screen.queryByText(source.content)).not.toBeInTheDocument(),
    );
    expect(
      await screen.findByText(
        "Nenhuma mensagem disponível para esta consulta.",
      ),
    ).toBeInTheDocument();
  });
  it("shows original messages and searches within the selected conversation", async () => {
    const api = new Api({ url: "http://localhost:8000", token: "synthetic" });
    vi.spyOn(api, "conversation").mockResolvedValue({
      conversation,
      messages: [source],
    });
    const search = vi
      .spyOn(api, "search")
      .mockResolvedValue([{ ...source, content: "Outro trecho sintético." }]);
    render(
      <ConversationExplorer
        api={api}
        connected
        conversationId={conversation.id}
        onChanged={() => undefined}
      />,
    );
    expect(await screen.findByText(source.content)).toBeInTheDocument();
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Buscar mensagens"), "prazo");
    await user.click(
      screen.getByRole("button", { name: "Buscar na conversa" }),
    );
    expect(
      await screen.findByText("Outro trecho sintético."),
    ).toBeInTheDocument();
    expect(search.mock.calls[0][0].get("conversation_id")).toBe(
      conversation.id,
    );
  });

  it("requires confirmation before scheduling provider work", async () => {
    const api = new Api({ url: "http://localhost:8000", token: "synthetic" });
    vi.spyOn(api, "conversation").mockResolvedValue({
      conversation,
      messages: [source],
    });
    const schedule = vi.spyOn(api, "reprocess").mockResolvedValue({
      id: source.id,
      conversation_id: conversation.id,
      filename: "Synthetic reindex",
      status: "completed",
      total_messages: 1,
      imported_messages: 1,
      processed_messages: 1,
      attempts: 0,
      error_message: null,
      created_at: source.sent_at,
      completed_at: source.sent_at,
    });
    render(
      <ConversationExplorer
        api={api}
        connected
        conversationId={conversation.id}
        onChanged={() => undefined}
      />,
    );
    const user = userEvent.setup();
    await user.click(
      screen.getByRole("button", { name: "Reprocessar memórias e embeddings" }),
    );
    expect(schedule).not.toHaveBeenCalled();
    await user.click(
      screen.getByRole("button", { name: "Confirmar reprocessamento" }),
    );
    await waitFor(() => expect(schedule).toHaveBeenCalledWith(conversation.id));
    expect(
      await screen.findByText("Reprocessamento concluído."),
    ).toBeInTheDocument();
  });
});
