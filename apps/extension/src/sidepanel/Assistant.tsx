import { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowUp,
  Import,
  Trash2,
  ChevronDown,
  MessageSquare,
  SlidersHorizontal,
} from "lucide-react";
import type { Answer, Contact } from "@whatsapp-memory/shared-types";
import type { Api } from "../services/api";
import { InlineText } from "../components/InlineText";
import { Evidence } from "../components/Evidence";
import { usd } from "../components/ApiUsage";
import { errorMessage } from "./App";

const allSources = (answer: Answer) => [
  ...(answer.context_revisions ?? []),
  ...answer.sources,
  ...(answer.context_sources ?? []),
];

interface Turn {
  id: number;
  question: string;
  scope?: string;
  answer?: Answer;
  error?: string;
}
const researchLabels: Record<string, string> = {
  read_conversation: "Leitura cronológica da conversa",
  answer_specific: "Resposta precisa com fontes verificadas",
  answer_question: "Resposta com fontes verificadas",
  search_timeline: "Pesquisa de mudanças ao longo dos meses",
  find_contacts: "Localização de contatos pela autoria",
  list_conversations: "Consulta ao catálogo de conversas",
  search_messages: "Busca de mensagens por assunto",
  read_messages: "Leitura de mensagens da conversa",
  expand_context: "Ampliação do contexto",
  search_memories: "Consulta a memórias e suas fontes",
  finish_research: "Seleção das fontes para responder",
};
export function Assistant({
  api,
  connected,
  conversationId,
  contactId,
  contacts,
  onContact,
  onImport,
  onConnect,
  onRefresh,
  validationEpoch = 0,
}: {
  api: Api;
  connected: boolean;
  conversationId: string;
  contactId: string;
  contacts: Contact[];
  onContact: (value: string) => void;
  onImport: () => void;
  onConnect?: () => void;
  onRefresh?: () => void;
  validationEpoch?: number;
}) {
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const nextId = useRef(0);
  const transcript = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const list = transcript.current;
    if (list) list.scrollTop = list.scrollHeight;
  }, [turns.length, busy]);
  const references = useMemo(
    () => [
      ...new Map(
        turns
          .flatMap(
            (turn) =>
              (turn.answer ? allSources(turn.answer) : []).map((source) => ({
                id: source.id,
                revision: source.revision ?? 1,
              })) ?? [],
          )
          .map((source) => [`${source.id}:${source.revision}`, source]),
      ).values(),
    ],
    [turns],
  );
  const validationKey = JSON.stringify([validationEpoch, references]);
  const [validation, setValidation] = useState<{
    api: Api;
    key: string;
    error: string;
  } | null>(null);
  const verified =
    connected &&
    (!references.length ||
      (validation?.api === api &&
        validation.key === validationKey &&
        !validation.error));
  useEffect(() => {
    if (!connected || !references.length) return;
    let active = true,
      pending = false;
    const check = async () => {
      if (pending) return;
      pending = true;
      try {
        const valid = await api.validateSources(references);
        if (!active) return;
        const validKeys = new Set(
          valid.map((source) => `${source.id}:${source.revision}`),
        );
        const invalid = new Set(
          references
            .filter(
              (source) => !validKeys.has(`${source.id}:${source.revision}`),
            )
            .map((source) => `${source.id}:${source.revision}`),
        );
        if (invalid.size)
          setTurns((items) =>
            items.map((turn) =>
              (turn.answer ? allSources(turn.answer) : []).some((source) =>
                invalid.has(`${source.id}:${source.revision ?? 1}`),
              )
                ? {
                    id: turn.id,
                    question: turn.question,
                    error:
                      "Uma fonte desta resposta foi alterada ou removida. Faça a pergunta novamente para consultar o conteúdo atual.",
                  }
                : turn,
            ),
          );
        setValidation({ api, key: validationKey, error: "" });
      } catch (error) {
        if (active)
          setValidation({
            api,
            key: validationKey,
            error: errorMessage(error),
          });
      } finally {
        pending = false;
      }
    };
    void check();
    const focus = () => {
      if (!document.hidden) void check();
    };
    const timer = setInterval(() => void check(), 10000);
    window.addEventListener("focus", focus);
    document.addEventListener("visibilitychange", focus);
    return () => {
      active = false;
      clearInterval(timer);
      window.removeEventListener("focus", focus);
      document.removeEventListener("visibilitychange", focus);
    };
  }, [api, connected, references, validationKey]);
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!question.trim() || busy || !connected) return;
    const value = question.trim(),
      id = nextId.current++;
    const scope = JSON.stringify([conversationId, contactId]);
    const previous = [...turns]
      .reverse()
      .find(
        (turn) =>
          turn.scope === scope &&
          turn.answer &&
          !turn.answer.insufficient_evidence,
      );
    setTurns((items) => [...items, { id, question: value, scope }].slice(-20));
    setQuestion("");
    setBusy(true);
    try {
      const answer = await api.query(
        value,
        conversationId || undefined,
        contactId || undefined,
        previous?.question,
      );
      setTurns((items) =>
        items.map((item) => (item.id === id ? { ...item, answer } : item)),
      );
    } catch (error) {
      setTurns((items) =>
        items.map((item) =>
          item.id === id ? { ...item, error: errorMessage(error) } : item,
        ),
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="assistant-section" aria-label="Assistente">
      <details className="chat-tools">
        <summary aria-label="Opções do chat" title="Opções do chat">
          <SlidersHorizontal size={16} />
          <span>Opções</span>
        </summary>
        <div className="chat-tools-content">
          {contacts.length > 0 && (
            <details className="author-filter">
              <summary>
                {contactId ? "Autor filtrado" : "Filtrar por autor"}
              </summary>
              <label>
                Autor das mensagens
                <select
                  value={contactId}
                  onChange={(event) => onContact(event.target.value)}
                >
                  <option value="">Todos os autores</option>
                  {contacts.map((contact) => (
                    <option key={contact.id} value={contact.id}>
                      {contact.display_name}
                    </option>
                  ))}
                </select>
              </label>
              <p className="fine-print">
                Restringe a leitura às mensagens enviadas por esse autor.
              </p>
            </details>
          )}
          <button
            className="text-button"
            title="Limpar conversa"
            aria-label="Limpar conversa"
            disabled={!turns.length || busy}
            onClick={() => setTurns([])}
          >
            <Trash2 size={16} />
            Limpar conversa
          </button>
          {onRefresh && (
            <button
              className="text-button"
              disabled={!connected || busy}
              onClick={onRefresh}
            >
              Atualizar conversas
            </button>
          )}
          <details className="privacy-note">
            <summary>Sobre este chat</summary>
            <p>
              As últimas 20 perguntas ficam apenas neste painel e são
              descartadas ao fechá-lo.
            </p>
          </details>
        </div>
      </details>
      {validation?.api === api &&
        validation.key === validationKey &&
        validation.error && (
          <p role="alert" className="notice error">
            Não foi possível revalidar as fontes: {validation.error}
          </p>
        )}
      <div className="turns" aria-live="polite" ref={transcript}>
        {!turns.length && (
          <div className="assistant-empty">
            <h1>
              Relembre a conversa.
              <br />
              Entenda o contexto.
            </h1>
            <p>
              {connected
                ? "Escolha uma conversa e pergunte sobre uma pessoa, um assunto ou o que ficou combinado."
                : "Conecte a MemorIA ao seu servidor para consultar suas conversas."}
            </p>
            {!connected && onConnect && (
              <button className="primary-button" onClick={onConnect}>
                Configurar conexão
              </button>
            )}
            {connected && (
              <div className="example-questions">
                <button
                  onClick={() =>
                    setQuestion(
                      "O que sei sobre essa pessoa pelas nossas conversas?",
                    )
                  }
                >
                  Quero conhecer o contexto completo
                </button>
                <button
                  onClick={() =>
                    setQuestion("Qual foi o último prazo combinado?")
                  }
                >
                  Quero recuperar um detalhe
                </button>
              </div>
            )}
            <button className="text-button" onClick={onImport}>
              <Import size={14} />
              Importar conversa
            </button>
          </div>
        )}

        {turns.map((turn) => (
          <article className="turn" key={turn.id}>
            <div className="question">
              <p>{turn.question}</p>
            </div>
            {turn.error ? (
              <div className="notice error" role="alert">
                {turn.error}
              </div>
            ) : turn.answer && !verified ? (
              <p className="fine-print">
                {connected
                  ? "Verificando se as fontes continuam válidas…"
                  : "Reconecte ao backend para revalidar as fontes desta resposta."}
              </p>
            ) : turn.answer ? (
              <div className="answer">
                <div className="answer-author">
                  <img
                    src="/brand/memoria-64.png"
                    alt=""
                    width="27"
                    height="27"
                  />
                  <strong>MemorIA</strong>
                </div>
                {turn.answer.insufficient_evidence ? (
                  <p>
                    {turn.answer.clarification ??
                      "O histórico disponível não estabelece uma resposta para essa pergunta."}
                  </p>
                ) : (
                  <>
                    {turn.answer.claims.map((claim, index) => (
                      <div className="claim" key={index}>
                        {claim.section && (
                          <h2>
                            <InlineText text={claim.section} />
                          </h2>
                        )}
                        <p>
                          <InlineText text={claim.text} />
                          {"\u00a0"}
                          <span className="citation-refs">
                            {claim.source_message_ids.map((id) => {
                              const number =
                                turn.answer?.sources.findIndex(
                                  (source) => source.id === id,
                                ) ?? -1;
                              return number < 0 ? null : (
                                <button
                                  key={id}
                                  aria-label={`Ver mensagem ${number + 1}`}
                                  onClick={() => {
                                    const drawer = document.getElementById(
                                      `sources-${turn.id}`,
                                    );
                                    if (drawer instanceof HTMLDetailsElement) {
                                      drawer.open = true;
                                      const original = drawer.querySelector(
                                        `[data-source-id="${id}"]`,
                                      );
                                      if (
                                        original instanceof HTMLDetailsElement
                                      )
                                        original.open = true;
                                      drawer.scrollIntoView?.({
                                        block: "nearest",
                                        behavior:
                                          typeof window.matchMedia ===
                                            "function" &&
                                          !window.matchMedia(
                                            "(prefers-reduced-motion: reduce)",
                                          ).matches
                                            ? "smooth"
                                            : "auto",
                                      });
                                    }
                                  }}
                                >
                                  {number + 1}
                                </button>
                              );
                            })}
                          </span>
                        </p>
                      </div>
                    ))}
                    <details
                      className="answer-sources"
                      id={`sources-${turn.id}`}
                    >
                      <summary>
                        <MessageSquare size={18} />
                        <span>
                          {turn.answer.sources.length}{" "}
                          {turn.answer.sources.length === 1
                            ? "mensagem original"
                            : "mensagens originais"}
                        </span>
                        <ChevronDown className="disclosure-chevron" size={18} />
                      </summary>
                      <Evidence sources={turn.answer.sources} />
                    </details>
                  </>
                )}
                {!!turn.answer.research_steps?.length && (
                  <details>
                    <summary>
                      Pesquisa realizada ({turn.answer.research_steps.length}{" "}
                      etapas)
                    </summary>
                    <ol className="fine-print">
                      {turn.answer.research_steps.map((step, index) => (
                        <li key={index}>
                          {researchLabels[step.tool] ?? "Consulta de dados"}:{" "}
                          {step.results}{" "}
                          {step.results === 1 ? "resultado" : "resultados"}
                          {step.status === "invalid_request"
                            ? " · solicitação recusada"
                            : ""}
                        </li>
                      ))}
                    </ol>
                  </details>
                )}
                {turn.answer.research_limited &&
                  !turn.answer.insufficient_evidence && (
                    <p className="fine-print">
                      A pesquisa atingiu um limite de contexto. A resposta cobre
                      somente as fontes consultadas.
                    </p>
                  )}
                {!!turn.answer.context_sources?.length && (
                  <details>
                    <summary>Contexto analisado pela IA</summary>
                    <p className="fine-print">
                      Estas mensagens fizeram parte do contexto. Elas não são
                      apresentadas como citações do modelo.
                    </p>
                    <Evidence sources={turn.answer.context_sources} />
                  </details>
                )}
                {turn.answer.provider === "mock" && (
                  <p className="fine-print">
                    Modo mock: trechos recuperados por busca, sem interpretação
                    de uma IA externa.
                  </p>
                )}
                {turn.answer.cost && (
                  <p className="fine-print">
                    {turn.answer.cost.estimated_usd === null
                      ? "Custo pendente de confirmação"
                      : `Custo estimado: ${usd(turn.answer.cost.estimated_usd, 4)}`}
                  </p>
                )}
              </div>
            ) : (
              <p role="status" className="loading-text">
                Pesquisando nas conversas e verificando as fontes…
              </p>
            )}
          </article>
        ))}
      </div>
      <form className="composer" onSubmit={(event) => void submit(event)}>
        <label htmlFor="question" className="sr-only">
          Pergunte ao seu histórico
        </label>
        <textarea
          id="question"
          placeholder="Pergunte sobre a conversa…"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          maxLength={2000}
          rows={1}
          onKeyDown={(event) => {
            if (
              event.key === "Enter" &&
              !event.shiftKey &&
              !event.nativeEvent.isComposing
            ) {
              event.preventDefault();
              event.currentTarget.form?.requestSubmit();
            }
          }}
          disabled={busy}
        />
        <div className="composer-bottom">
          <button
            className="send-button"
            type="submit"
            aria-label="Enviar pergunta"
            disabled={!connected || busy || !question.trim()}
          >
            <ArrowUp size={19} />
          </button>
        </div>
      </form>
    </section>
  );
}
