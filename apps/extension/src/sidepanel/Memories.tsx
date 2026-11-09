import { useEffect, useState } from "react";
import { BookOpen, Trash2 } from "lucide-react";
import type { Contact, Memory } from "@whatsapp-memory/shared-types";
import type { Api } from "../services/api";
import { Evidence, formatDate } from "../components/Evidence";
import { errorMessage } from "./App";

const labels = {
  fact: "Fato",
  event: "Evento",
  intention: "Intenção",
  commitment: "Compromisso",
};
const statuses = {
  active: "Ativa",
  outdated: "Desatualizada",
  disputed: "Em disputa",
  deleted: "Excluída",
};
export function Memories({
  api,
  connected,
  contacts,
  revision,
}: {
  api: Api;
  connected: boolean;
  contacts: Contact[];
  revision: number;
}) {
  const [type, setType] = useState(""),
    [contact, setContact] = useState(""),
    [offset, setOffset] = useState(0),
    [mutating, setMutating] = useState(false),
    [error, setError] = useState(""),
    [deleting, setDeleting] = useState("");
  const [reload, setReload] = useState(0);
  const [result, setResult] = useState<{
    key: string;
    api: Api;
    items: Memory[];
    error: string;
  } | null>(null);
  const requestKey = JSON.stringify([type, contact, offset, revision, reload]);
  const current = connected && result?.key === requestKey && result.api === api;
  const items = current ? result.items : [];
  const busy = mutating || (connected && !current);
  const loadError = current ? result.error : "";
  useEffect(() => {
    let live = true;
    if (!connected) return;
    const params = new URLSearchParams({ limit: "30", offset: String(offset) });
    if (type) params.set("memory_type", type);
    if (contact) params.set("contact_id", contact);
    let pending = false;
    const load = () => {
      if (pending) return;
      pending = true;
      void api
        .memories(params)
        .then((data) => {
          if (live) setResult({ key: requestKey, api, items: data, error: "" });
        })
        .catch((error) => {
          if (live)
            setResult({
              key: requestKey,
              api,
              items: [],
              error: errorMessage(error),
            });
        })
        .finally(() => {
          pending = false;
        });
    };
    load();
    const timer = setInterval(load, 10000);
    window.addEventListener("focus", load);
    return () => {
      live = false;
      clearInterval(timer);
      window.removeEventListener("focus", load);
    };
  }, [api, connected, type, contact, offset, requestKey]);
  async function remove(id: string) {
    setMutating(true);
    try {
      await api.deleteMemory(id);
      setDeleting("");
      setReload((value) => value + 1);
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setMutating(false);
    }
  }
  return (
    <section>
      <div className="section-heading">
        <h1>Memórias</h1>
        <BookOpen size={21} />
      </div>
      <p className="section-description">
        Fatos, eventos, intenções e compromissos. Cada registro leva de volta à
        conversa.
      </p>
      <details className="filter-panel">
        <summary>
          {type || contact ? "Filtros ativos" : "Filtrar memórias"}
        </summary>
        <div className="form-row filters">
          <label>
            Tipo
            <select
              value={type}
              onChange={(event) => {
                setType(event.target.value);
                setOffset(0);
              }}
            >
              <option value="">Todos os tipos</option>
              {Object.entries(labels).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label>
            Contato
            <select
              value={contact}
              onChange={(event) => {
                setContact(event.target.value);
                setOffset(0);
              }}
            >
              <option value="">Todos os contatos</option>
              {contacts.map((person) => (
                <option key={person.id} value={person.id}>
                  {person.display_name} · {person.conversation_id.slice(0, 8)}
                </option>
              ))}
            </select>
          </label>
        </div>
      </details>
      {busy && <p role="status">Carregando memórias…</p>}
      {(error || loadError) && (
        <p role="alert" className="notice error">
          {error || loadError}
        </p>
      )}
      {!busy && !items.length && (
        <div className="empty-state">
          <BookOpen size={28} />
          <h2>Nenhuma memória por aqui.</h2>
          <p>
            Os registros aparecem após o processamento das conversas. Cada
            memória mantém as mensagens que a sustentam.
          </p>
        </div>
      )}
      {items.map((memory) => (
        <article className="memory" key={memory.id}>
          <div className="memory-meta">
            <span className="tag">{labels[memory.memory_type]}</span>
            <span>{statuses[memory.status]}</span>
            <button
              className="icon-button"
              aria-label="Excluir memória"
              onClick={() => setDeleting(memory.id)}
            >
              <Trash2 size={15} />
            </button>
          </div>
          {memory.evidence_basis === "interpretation" && (
            <p className="fine-print">
              Interpretação da IA · confira as mensagens de suporte.
            </p>
          )}
          <p>{memory.content}</p>
          <div className="memory-details">
            <time>{formatDate(memory.observed_at)}</time>
            <details>
              <summary>Detalhes da memória</summary>
              <span>Confiança {(memory.confidence * 100).toFixed(0)}%</span>
            </details>
          </div>
          <Evidence sources={memory.sources} />
          {!!memory.context_sources?.length && (
            <details>
              <summary>Contexto usado na extração</summary>
              <p className="fine-print">
                Mensagens fornecidas ao modelo, separadas das citações.
                Alterá-las invalida esta memória.
              </p>
              <Evidence sources={memory.context_sources} />
            </details>
          )}
          {deleting === memory.id && (
            <div className="delete-confirm">
              <p>
                Excluir esta memória e seu vetor? As mensagens originais
                permanecem. O registro de exclusão impede que ela seja recriada.
              </p>
              <button
                className="danger-button"
                disabled={busy}
                onClick={() => void remove(memory.id)}
              >
                Confirmar exclusão
              </button>
              <button className="text-button" onClick={() => setDeleting("")}>
                Cancelar
              </button>
            </div>
          )}
        </article>
      ))}
      <div className="pagination">
        <button
          className="text-button"
          disabled={!offset || busy}
          onClick={() => setOffset((value) => Math.max(0, value - 30))}
        >
          Anterior
        </button>
        <span>Página {Math.floor(offset / 30) + 1}</span>
        <button
          className="text-button"
          disabled={items.length < 30 || busy}
          onClick={() => setOffset((value) => value + 30)}
        >
          Próxima
        </button>
      </div>
    </section>
  );
}
