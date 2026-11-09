import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { it, expect, vi } from "vitest";
import type { Answer } from "@whatsapp-memory/shared-types";
import { Assistant } from "./Assistant";
import { Api } from "../services/api";

it.each(["citation", "context", "hidden-context"])(
  "removes an old answer when its %s changes after generation",
  async (changed) => {
    const id = "11111111-1111-4111-8111-111111111111";
    const text = "Declaração sintética que será revogada.";
    const answer: Answer = {
      claims: [
        {
          kind: "explicit_statement",
          text,
          quote: text,
          source_message_ids: [id],
        },
      ],
      insufficient_evidence: false,
      provider: "mock",
      sources: [
        {
          id,
          conversation_id: "22222222-2222-4222-8222-222222222222",
          sender_contact_id: null,
          sender_name_snapshot: "Alice",
          content: text,
          message_type: "text",
          sent_at: "2026-11-03T10:00:00Z",
          revision: 1,
        },
      ],
    };
    if (changed === "context") {
      const source = answer.sources[0];
      if (!source) throw new Error("Missing fixture source");
      answer.context_sources = [
        {
          ...source,
          id: "33333333-3333-4333-8333-333333333333",
          content: "Pergunta sintética que contextualizou a resposta.",
        },
      ];
    }
    if (changed === "hidden-context")
      answer.context_revisions = [
        { id: "33333333-3333-4333-8333-333333333333", revision: 1 },
      ];
    const api = new Api({ url: "http://localhost:8000", token: "synthetic" });
    vi.spyOn(api, "query").mockResolvedValue(answer);
    const validate = vi
      .spyOn(api, "validateSources")
      .mockResolvedValueOnce(
        [
          ...answer.sources,
          ...(answer.context_sources ?? []),
          ...(answer.context_revisions ?? []),
        ].map((source) => ({ id: source.id, revision: 1 })),
      )
      .mockResolvedValue(changed !== "citation" ? [{ id, revision: 1 }] : []);
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
    const user = userEvent.setup();
    await user.type(
      screen.getByLabelText("Pergunte ao seu histórico"),
      "O que foi dito?",
    );
    await user.click(screen.getByRole("button", { name: "Enviar pergunta" }));
    await screen.findByText(text);
    expect(validate).toHaveBeenCalledTimes(1);
    await act(async () => {
      window.dispatchEvent(new Event("focus"));
    });
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(
        "foi alterada ou removida",
      ),
    );
    expect(screen.queryByText(text)).not.toBeInTheDocument();
    expect(screen.queryByText("Alice")).not.toBeInTheDocument();
  },
);
