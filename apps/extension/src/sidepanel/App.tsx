import { useEffect, useState } from "react";
import {
  BookOpen,
  ChevronRight,
  Import,
  MessageSquare,
  Settings2,
  Search,
  ArrowLeft,
  UserRound,
} from "lucide-react";
import type {
  Contact,
  Conversation,
  Health,
} from "@whatsapp-memory/shared-types";
import { useConnection } from "../hooks/useConnection";
import { Assistant } from "./Assistant";
import { ConversationExplorer } from "./ConversationExplorer";
import { ImportPanel } from "./ImportPanel";
import { Memories } from "./Memories";
import { SettingsPanel } from "./Settings";

type Tab = "assistant" | "memories" | "import" | "settings";
const tabs = [
  { id: "assistant", label: "Chat", icon: MessageSquare },
  { id: "memories", label: "Memórias", icon: BookOpen },
  { id: "import", label: "Importar", icon: Import },
  { id: "settings", label: "Ajustes", icon: Settings2 },
] satisfies { id: Tab; label: string; icon: typeof MessageSquare }[];
export const errorMessage = (error: unknown) =>
  error instanceof Error
    ? error.message
    : "Ocorreu um erro inesperado. Tente novamente.";

export function App() {
  const { connection, save, ready, api } = useConnection();
  const [tab, setTab] = useState<Tab>("assistant");
  const [exploring, setExploring] = useState(false);
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState("");
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [selected, setSelected] = useState("");
  const [contact, setContact] = useState("");
  const [revision, setRevision] = useState(0);
  const [connectionRevision, setConnectionRevision] = useState(0);
  const refresh = () => setConnectionRevision((value) => value + 1);
  useEffect(() => {
    if (!ready || !connection.token) return;
    let live = true;
    void Promise.all([api.health(), api.conversations(), api.contacts()])
      .then(([status, chats, people]) => {
        if (!live) return;
        setError("");
        setHealth(status);
        setConversations(chats);
        setContacts(people);
        setSelected((value) =>
          chats.some((chat) => chat.id === value) ? value : "",
        );
      })
      .catch((error) => {
        if (live) {
          setError(errorMessage(error));
          setHealth(null);
        }
      });
    return () => {
      live = false;
    };
  }, [ready, api, connection.token, connectionRevision]);
  const loading = !!connection.token && !health && !error;
  const changed = () => {
    setContact("");
    setRevision((value) => value + 1);
    void refresh();
  };
  return (
    <div className="app-shell">
      <header className="app-header">
        <img
          className="brand-logo"
          src="/brand/memoria-64.png"
          width="32"
          height="32"
          alt=""
        />
        <div className="brand">
          <strong>MemorIA</strong>
        </div>
        <button
          className="header-status"
          onClick={() => setTab("settings")}
          aria-label={
            health
              ? "Servidor conectado. Abrir ajustes"
              : "Servidor desconectado. Abrir ajustes"
          }
        >
          <span
            className={health ? "connection-dot online" : "connection-dot"}
          />
          {loading ? "Conectando…" : health ? "Conectado" : "Desconectado"}
        </button>
      </header>
      <main className={tab === "assistant" ? "chat-main" : undefined}>
        {!connection.token && tab !== "settings" && tab !== "assistant" && (
          <div className="connection-banner">
            <Settings2 size={18} />
            <div>
              <strong>Conecte seu arquivo de memórias</strong>
              <p>Inicie o backend local e configure a conexão para começar.</p>
              <button
                className="text-button"
                onClick={() => setTab("settings")}
              >
                Configurar conexão <ChevronRight size={15} />
              </button>
            </div>
          </div>
        )}
        {error && (
          <div role="alert" className="notice error">
            {error}
            <button className="text-button" onClick={() => void refresh()}>
              Tentar conectar novamente
            </button>
          </div>
        )}
        <div className="chat-panel" hidden={tab !== "assistant"}>
          <div className="context-bar">
            <label htmlFor="conversation" className="sr-only">
              Consultar
            </label>
            <UserRound className="scope-person" size={19} aria-hidden="true" />
            <select
              id="conversation"
              disabled={!health}
              value={selected}
              onChange={(event) => {
                setSelected(event.target.value);
                setContact("");
              }}
            >
              <option value="">Todas as conversas</option>
              {conversations.map((chat) => (
                <option key={chat.id} value={chat.id}>
                  {chat.title}
                </option>
              ))}
            </select>
            <button
              className="scope-search"
              disabled={!health}
              aria-label={
                exploring ? "Voltar ao assistente" : "Explorar mensagens"
              }
              title={exploring ? "Voltar ao assistente" : "Explorar mensagens"}
              onClick={() => setExploring((value) => !value)}
            >
              {exploring ? <ArrowLeft size={22} /> : <Search size={22} />}
            </button>
          </div>
          {exploring && (
            <ConversationExplorer
              api={api}
              connected={!!health}
              conversationId={selected}
              onChanged={changed}
            />
          )}
          <div className="assistant-host" hidden={exploring}>
            <Assistant
              key={connection.url}
              api={api}
              connected={!!health}
              validationEpoch={connectionRevision}
              conversationId={selected}
              contactId={contact}
              contacts={contacts.filter(
                (item) => !selected || item.conversation_id === selected,
              )}
              onContact={setContact}
              onImport={() => setTab("import")}
              onConnect={() => setTab("settings")}
              onRefresh={refresh}
            />
          </div>
        </div>
        {tab === "memories" && (
          <Memories
            api={api}
            connected={!!health}
            contacts={contacts}
            revision={revision}
          />
        )}
        {tab === "import" && (
          <ImportPanel
            api={api}
            connected={!!health}
            conversations={conversations}
            maxBytes={health?.import_max_bytes ?? 5_000_000}
            onImported={changed}
          />
        )}
        {tab === "settings" && (
          <SettingsPanel
            connection={connection}
            save={save}
            health={health}
            api={api}
            conversations={conversations}
            onChanged={changed}
            onMoreConversations={async () => {
              const more = await api.conversations(conversations.length);
              setConversations([...conversations, ...more]);
            }}
            onMoreContacts={async () => {
              const more = await api.contacts(contacts.length);
              setContacts([...contacts, ...more]);
            }}
          />
        )}
      </main>
      <nav className="bottom-nav" aria-label="Navegação principal">
        {tabs.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            className={tab === id ? "nav-item selected" : "nav-item"}
            aria-current={tab === id ? "page" : undefined}
            onClick={() => setTab(id)}
          >
            <Icon size={23} strokeWidth={1.7} />
            <span>{label}</span>
          </button>
        ))}
      </nav>
    </div>
  );
}
