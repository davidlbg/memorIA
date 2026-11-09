import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, FileUp, RefreshCw } from "lucide-react";
import type { Conversation, Job, Preview } from "@whatsapp-memory/shared-types";
import type { Api } from "../services/api";
import { formatDate } from "../components/Evidence";
import { errorMessage } from "./App";

const statusNames = {
  pending: "Aguardando IA",
  processing: "Processando e indexando",
  completed: "Indexado",
  failed: "Falha no processamento",
};
export function ImportPanel({
  api,
  connected,
  conversations,
  maxBytes,
  onImported,
}: {
  api: Api;
  connected: boolean;
  conversations: Conversation[];
  maxBytes: number;
  onImported: () => void;
}) {
  const [file, setFile] = useState<File | null>(null),
    [title, setTitle] = useState(""),
    [order, setOrder] = useState("DMY"),
    [timezone, setTimezone] = useState("America/Sao_Paulo"),
    [conversationId, setConversationId] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [success, setSuccess] = useState("");
  const [jobs, setJobs] = useState<Job[]>([]),
    [more, setMore] = useState(true);
  const load = useCallback(async () => {
    if (connected) setJobs(await api.imports());
  }, [api, connected]);
  useEffect(() => {
    let live = true;
    if (connected)
      void api
        .imports()
        .then((data) => {
          if (live) setJobs(data);
        })
        .catch((error) => {
          if (live) setError(errorMessage(error));
        });
    return () => {
      live = false;
    };
  }, [api, connected]);
  const pending = jobs.some(
    (job) => job.status === "pending" || job.status === "processing",
  );
  useEffect(() => {
    if (!pending) return;
    const timer = setInterval(
      () => void load().catch((error) => setError(errorMessage(error))),
      3000,
    );
    return () => clearInterval(timer);
  }, [pending, load]);
  const invalidate = () => {
    setPreview(null);
    setSuccess("");
  };
  function form(): FormData {
    if (!file) throw new Error("Selecione um arquivo TXT.");
    const data = new FormData();
    data.set("file", file);
    data.set("date_order", order);
    data.set("timezone", timezone);
    data.set("title", title);
    if (conversationId) data.set("conversation_id", conversationId);
    if (preview) data.set("preview_hash", preview.file_hash);
    return data;
  }
  async function inspect(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setSuccess("");
    try {
      if (!file) throw new Error("Selecione um arquivo TXT.");
      if (file.size > maxBytes)
        throw new Error(
          `Arquivo excede ${(maxBytes / 1_000_000).toFixed(1)} MB.`,
        );
      setPreview(await api.preview(form()));
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }
  async function commit() {
    setBusy(true);
    setError("");
    try {
      const job = await api.import(form());
      setSuccess(
        `${job.imported_messages} mensagens novas importadas. ${job.total_messages - job.imported_messages} já existentes.`,
      );
      setPreview(null);
      await load();
      onImported();
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }
  async function retry(id: string) {
    try {
      await api.retry(id);
      await load();
    } catch (error) {
      setError(errorMessage(error));
    }
  }
  return (
    <section>
      <div className="section-heading">
        <h1>Traga uma conversa.</h1>
        <FileUp size={21} />
      </div>
      <p className="section-description">
        Exporte o histórico no WhatsApp, sem mídia. Você confere as mensagens
        antes de importar.
      </p>
      {!connected && (
        <p className="notice">
          Conecte o servidor em Ajustes para conferir e importar o arquivo.
        </p>
      )}
      <form className="import-form" onSubmit={(event) => void inspect(event)}>
        <label className="file-drop">
          <FileUp size={26} strokeWidth={1.5} />
          <strong>{file?.name ?? "Selecionar exportação .txt"}</strong>
          <span>
            UTF-8 ou UTF-16 · até {(maxBytes / 1_000_000).toFixed(1)} MB
          </span>
          <input
            type="file"
            accept=".txt,text/plain"
            aria-label="Exportação TXT"
            onChange={(event) => {
              const chosen = event.target.files?.[0] ?? null;
              setFile(chosen);
              setTitle(chosen?.name.replace(/\.txt$/i, "") ?? "");
              invalidate();
            }}
          />
        </label>
        <details className="import-options" open={!!file}>
          <summary>
            {file
              ? "Conferir dados da importação"
              : "Dados e formato do arquivo"}
          </summary>
          <div className="import-fields">
            <label>
              Nome da conversa
              <input
                value={title}
                onChange={(event) => {
                  setTitle(event.target.value);
                  invalidate();
                }}
                required
                maxLength={200}
                placeholder="Ex.: Equipe do projeto"
              />
            </label>
            <label>
              Destino
              <select
                value={conversationId}
                onChange={(event) => {
                  setConversationId(event.target.value);
                  invalidate();
                }}
              >
                <option value="">
                  Criar conversa (ou reconhecer arquivo idêntico)
                </option>
                {conversations.map((chat) => (
                  <option key={chat.id} value={chat.id}>
                    Adicionar a: {chat.title}
                  </option>
                ))}
              </select>
            </label>
            <div className="form-row">
              <label>
                Ordem da data
                <select
                  value={order}
                  onChange={(event) => {
                    setOrder(event.target.value);
                    invalidate();
                  }}
                >
                  <option value="DMY">Dia / mês / ano</option>
                  <option value="MDY">Mês / dia / ano</option>
                  <option value="YMD">Ano / mês / dia</option>
                </select>
              </label>
              <label>
                Fuso do histórico
                <input
                  value={timezone}
                  onChange={(event) => {
                    setTimezone(event.target.value);
                    invalidate();
                  }}
                  required
                  maxLength={100}
                  list="timezones"
                />
                <datalist id="timezones">
                  <option value="America/Sao_Paulo" />
                  <option value="UTC" />
                  <option value="Europe/Lisbon" />
                </datalist>
              </label>
            </div>
          </div>
        </details>
        <button
          className="primary-button full"
          disabled={!connected || !file || busy}
          type="submit"
        >
          {busy ? "Verificando…" : "Conferir prévia"}
        </button>
      </form>
      {error && (
        <div role="alert" className="notice error">
          {error}
        </div>
      )}
      {success && (
        <div role="status" className="notice success">
          <CheckCircle2 size={18} />
          {success}
        </div>
      )}
      {preview && (
        <div className="preview">
          <h2>{preview.total_messages} mensagens reconhecidas</h2>
          <p>
            {preview.participants.join(", ") || "Mensagens de sistema"} ·{" "}
            {preview.formats.join(", ")}
          </p>
          <div className="preview-messages">
            {preview.messages.slice(0, 10).map((message, index) => (
              <div key={index}>
                <strong>{message.sender ?? "Sistema"}</strong>
                <time>{formatDate(message.sent_at)}</time>
                <p>{message.content}</p>
                {message.message_type === "media_reference" && (
                  <span className="tag">
                    Referência de mídia; arquivo não importado
                  </span>
                )}
              </div>
            ))}
          </div>
          <p className="fine-print">
            Exibindo as primeiras {Math.min(10, preview.total_messages)}{" "}
            mensagens.
          </p>
          {preview.warnings.map((warning) => (
            <p className="fine-print" key={warning}>
              {warning}
            </p>
          ))}
          <button
            className="primary-button full"
            disabled={busy}
            onClick={() => void commit()}
          >
            Confirmar importação
          </button>
        </div>
      )}
      <div className="section-heading history-heading">
        <h2>Histórico de importações</h2>
        <button
          className="icon-button"
          aria-label="Atualizar importações"
          onClick={() =>
            void load().catch((error) => setError(errorMessage(error)))
          }
        >
          <RefreshCw size={16} />
        </button>
      </div>
      {!jobs.length && <p className="muted">Nenhum arquivo importado ainda.</p>}
      {jobs.map((job) => (
        <div className="job-row" key={job.id}>
          <div>
            <strong>{job.filename}</strong>
            <span>
              {formatDate(job.created_at)} · {job.imported_messages} novas
              mensagens
            </span>
            <span>
              {job.processed_messages}/{job.imported_messages} processadas
            </span>
          </div>
          <span className={`tag ${job.status === "failed" ? "danger" : ""}`}>
            {statusNames[job.status]}
          </span>
          {job.error_message && (
            <p className="fine-print">{job.error_message}</p>
          )}
          {job.status === "failed" && (
            <button className="text-button" onClick={() => void retry(job.id)}>
              Retomar processamento
            </button>
          )}
        </div>
      ))}
      {jobs.length >= 30 && more && (
        <button
          className="secondary-button full"
          onClick={() =>
            void api
              .imports(jobs.length)
              .then((items) => {
                setJobs([...jobs, ...items]);
                setMore(items.length === 30);
              })
              .catch((error) => setError(errorMessage(error)))
          }
        >
          Carregar mais importações
        </button>
      )}
    </section>
  );
}
