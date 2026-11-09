import { useEffect, useLayoutEffect, useState } from "react";
import type { Job, Source } from "@whatsapp-memory/shared-types";
import type { Api } from "../services/api";
import { Evidence, formatDate } from "../components/Evidence";
import { errorMessage } from "./App";

export function ConversationExplorer({
  api,
  connected,
  conversationId,
  onChanged,
}: {
  api: Api;
  connected: boolean;
  conversationId: string;
  onChanged: () => void;
}) {
  const [draft, setDraft] = useState("");
  const [query, setQuery] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [result, setResult] = useState<{
    api: Api;
    key: string;
    messages: Source[];
    more: boolean;
    error: string;
  } | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [actionError, setActionError] = useState("");
  const [jobState, setJobState] = useState<{
    api: Api;
    conversation: string;
    job: Job;
  } | null>(null);
  const key = JSON.stringify([conversationId, query, refresh]);
  const current =
    connected && result?.api === api && result.key === key ? result : null;
  const job =
    jobState?.api === api && jobState.conversation === conversationId
      ? jobState.job
      : null;
  const [scheduling, setScheduling] = useState(false);

  useEffect(() => {
    if (!connected || !conversationId) return;
    let active = true;
    const load = query
      ? api.search(
          new URLSearchParams({
            query,
            conversation_id: conversationId,
            limit: "50",
          }),
        )
      : api.conversation(conversationId).then((value) => value.messages);
    void load
      .then((messages) => {
        if (active)
          setResult({
            api,
            key,
            messages,
            more: !query && messages.length === 50,
            error: "",
          });
      })
      .catch((error) => {
        if (active)
          setResult({
            api,
            key,
            messages: [],
            more: false,
            error: errorMessage(error),
          });
      });
    return () => {
      active = false;
    };
  }, [api, connected, conversationId, query, key]);

  useLayoutEffect(() => {
    if (!current?.messages.length) return;
    let active = true,
      pending = false;
    const verify = async () => {
      if (pending) return;
      pending = true;
      try {
        for (let offset = 0; offset < current.messages.length; offset += 500) {
          const refs = current.messages
            .slice(offset, offset + 500)
            .map((source) => ({
              id: source.id,
              revision: source.revision ?? 1,
            }));
          const valid = await api.validateSources(refs);
          if (active && valid.length !== refs.length) {
            setResult(null);
            setRefresh((value) => value + 1);
            return;
          }
        }
      } catch (error) {
        if (active)
          setResult({
            api,
            key,
            messages: [],
            more: false,
            error: errorMessage(error),
          });
      } finally {
        pending = false;
      }
    };
    const focus = () => void verify();
    const timer = setInterval(focus, 10000);
    window.addEventListener("focus", focus);
    return () => {
      active = false;
      clearInterval(timer);
      window.removeEventListener("focus", focus);
    };
  }, [api, current, key]);

  useEffect(() => {
    if (!job || !["pending", "processing"].includes(job.status)) return;
    let active = true,
      pending = false;
    const poll = async () => {
      if (pending) return;
      pending = true;
      try {
        const updated = await api.job(job.id);
        if (active) {
          setJobState({ api, conversation: conversationId, job: updated });
          if (updated.status === "completed") onChanged();
        }
      } catch (error) {
        if (active) setActionError(errorMessage(error));
      } finally {
        pending = false;
      }
    };
    const timer = setInterval(() => void poll(), 3000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [api, conversationId, job, onChanged]);

  async function more() {
    const before = current?.messages.at(-1)?.id;
    if (!before || loadingMore) return;
    setLoadingMore(true);
    setActionError("");
    try {
      const page = await api.conversation(conversationId, before);
      setResult((value) =>
        value?.api === api && value.key === key
          ? {
              ...value,
              messages: [
                ...new Map(
                  [...value.messages, ...page.messages].map((message) => [
                    message.id,
                    message,
                  ]),
                ).values(),
              ],
              more: page.messages.length === 50,
            }
          : value,
      );
    } catch (error) {
      setActionError(errorMessage(error));
    } finally {
      setLoadingMore(false);
    }
  }
  async function reprocess() {
    setScheduling(true);
    setActionError("");
    try {
      const created = await api.reprocess(conversationId);
      setJobState({ api, conversation: conversationId, job: created });
      setConfirm(false);
    } catch (error) {
      setActionError(errorMessage(error));
    } finally {
      setScheduling(false);
    }
  }
  if (!conversationId)
    return (
      <p className="notice">
        Selecione uma conversa no campo Consultar para explorar suas mensagens.
      </p>
    );
  const messages = current?.messages ?? [];
  const displayed = query ? messages : [...messages].reverse();
  return (
    <section className="conversation-explorer" aria-label="Explorar conversa">
      <div className="section-heading">
        <h1>Mensagens originais</h1>
      </div>
      <p className="section-description">
        Mensagens originais de todos os participantes. Pesquise um assunto ou
        percorra a conversa.
      </p>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          setQuery(draft.trim());
          setRefresh((value) => value + 1);
        }}
        className="settings-form"
      >
        <label>
          Buscar mensagens
          <input
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            maxLength={2000}
            placeholder="Assunto, decisão ou compromisso"
          />
        </label>
        <button className="secondary-button" disabled={!connected}>
          Buscar na conversa
        </button>
      </form>
      {current?.error && (
        <p role="alert" className="notice error">
          {current.error}
        </p>
      )}
      {actionError && (
        <p role="alert" className="notice error">
          {actionError}
        </p>
      )}
      {connected && !current && <p role="status">Carregando mensagens…</p>}
      {current && !current.error && !messages.length && (
        <p>Nenhuma mensagem disponível para esta consulta.</p>
      )}
      {displayed.map((message) => (
        <article className="memory" key={message.id}>
          <div className="memory-meta">
            <strong>
              {message.sender_name_snapshot ?? "Mensagem do sistema"}
            </strong>
            <time dateTime={message.sent_at}>
              {formatDate(message.sent_at)}
            </time>
          </div>
          <p style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
            {message.content || "Mensagem sem corpo de texto"}
          </p>
          <Evidence sources={[message]} showContent={false} />
        </article>
      ))}
      {current?.more && (
        <button
          className="text-button"
          disabled={loadingMore}
          onClick={() => void more()}
        >
          {loadingMore ? "Carregando…" : "Carregar mensagens mais antigas"}
        </button>
      )}
      <details className="settings-section settings-disclosure">
        <summary>Processamento da conversa</summary>
        <p>
          Use após configurar ou atualizar o modelo de IA. As mensagens
          originais são preservadas; o worker atualiza os índices e extrai
          memórias com fontes.
        </p>
        {job && (
          <p role="status">
            {job.status === "completed"
              ? "Reprocessamento concluído."
              : job.status === "failed"
                ? job.error_message
                : `Processando ${job.processed_messages} de ${job.total_messages} mensagens…`}
          </p>
        )}
        {!confirm ? (
          <button
            className="text-button"
            disabled={
              !connected ||
              scheduling ||
              (!!job && ["pending", "processing"].includes(job.status))
            }
            onClick={() => setConfirm(true)}
          >
            Reprocessar memórias e embeddings
          </button>
        ) : (
          <div className="notice">
            <p>
              Reprocessar esta conversa com os provedores configurados no
              backend? Isso consome recursos do modelo selecionado.
            </p>
            <button
              className="secondary-button"
              disabled={scheduling}
              onClick={() => void reprocess()}
            >
              Confirmar reprocessamento
            </button>
            <button className="text-button" onClick={() => setConfirm(false)}>
              Cancelar
            </button>
          </div>
        )}
      </details>
    </section>
  );
}
