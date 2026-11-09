import { useState } from "react";
import { ShieldCheck, Trash2 } from "lucide-react";
import type { Conversation, Health } from "@whatsapp-memory/shared-types";
import { Api, type Connection, validateBackendUrl } from "../services/api";
import { errorMessage } from "./App";
import { WhatsAppConnection } from "../components/WhatsAppConnection";
import { OpenAISettings } from "../components/OpenAISettings";
import { ApiUsage } from "../components/ApiUsage";

export function SettingsPanel({
  connection,
  save,
  health,
  api,
  conversations,
  onChanged,
  onMoreConversations,
  onMoreContacts,
}: {
  connection: Connection;
  save: (value: Connection) => Promise<void>;
  health: Health | null;
  api: Api;
  conversations: Conversation[];
  onChanged: () => void;
  onMoreConversations: () => Promise<void>;
  onMoreContacts: () => Promise<void>;
}) {
  const [url, setUrl] = useState(connection.url),
    [token, setToken] = useState(connection.token),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [success, setSuccess] = useState(""),
    [deleting, setDeleting] = useState("");
  const [provider, setProvider] = useState<"mock" | "openai" | "ollama" | null>(
    null,
  );
  const selectedProvider = provider ?? health?.llm_provider ?? "mock";
  const [theme, setTheme] = useState(
    localStorage.getItem("memory-theme") ?? "system",
  );
  async function connect(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setSuccess("");
    try {
      const origin = validateBackendUrl(url);
      if (
        origin.startsWith("https:") &&
        typeof chrome !== "undefined" &&
        chrome.permissions
      ) {
        const granted = await chrome.permissions.request({
          origins: [`${origin}/*`],
        });
        if (!granted)
          throw new Error("Permissão ao backend HTTPS não concedida.");
      }
      const next = { url: origin, token };
      await new Api(next).health();
      await save(next);
      setSuccess(
        "Conexão verificada. Configuração salva nesta sessão do navegador.",
      );
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }
  async function remove(id: string) {
    setBusy(true);
    setError("");
    try {
      await api.deleteConversation(id);
      setDeleting("");
      onChanged();
      setSuccess(
        "Conversa, contatos, mensagens, memórias, vetores e importações excluídos.",
      );
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section>
      <div className="section-heading">
        <h1>Ajustes</h1>
        <ShieldCheck size={21} />
      </div>
      <p className="section-description">Conexões, aparência e seus dados.</p>
      <details className="settings-section settings-disclosure" open={!health}>
        <summary>
          Servidor{" "}
          <span className="summary-state">
            {health ? "Conectado" : "Configurar"}
          </span>
        </summary>
        <form
          className="settings-form"
          onSubmit={(event) => void connect(event)}
        >
          <label>
            Endereço do backend
            <input
              value={url}
              onChange={(event) => setUrl(event.target.value)}
              required
              type="url"
            />
          </label>
          <label>
            Token de acesso local
            <input
              value={token}
              onChange={(event) => setToken(event.target.value)}
              required
              type="password"
              autoComplete="off"
              minLength={32}
              placeholder="Cole o token do seu servidor"
            />
          </label>
          <p className="fine-print">
            O token dá acesso ao histórico. Ele fica no armazenamento de sessão
            da extensão, sem acesso por scripts de páginas.
          </p>
          <button className="primary-button" disabled={busy}>
            {busy ? "Verificando…" : "Verificar e salvar conexão"}
          </button>
        </form>
      </details>
      {error && (
        <p role="alert" className="notice error">
          {error}
        </p>
      )}
      {success && (
        <p role="status" className="notice success">
          {success}
        </p>
      )}
      <details className="settings-section settings-disclosure">
        <summary>WhatsApp</summary>
        <WhatsAppConnection api={api} enabled={!!connection.token} />
      </details>
      <details className="settings-section settings-disclosure">
        <summary>
          Inteligência e privacidade{" "}
          <span className="summary-state">
            {health?.llm_provider === "openai" ? "OpenAI" : "Configurar"}
          </span>
        </summary>
        <OpenAISettings
          api={api}
          health={health}
          connected={!!connection.token}
          onChanged={onChanged}
        />
        <details className="advanced-settings">
          <summary>Detalhes da IA e provedor</summary>
          <label>
            Provedor de respostas
            <select
              value={selectedProvider}
              onChange={(event) => {
                if (
                  event.target.value === "mock" ||
                  event.target.value === "openai" ||
                  event.target.value === "ollama"
                )
                  setProvider(event.target.value);
              }}
            >
              <option value="mock">Mock determinístico · local</option>
              <option value="ollama" disabled={!health?.ollama_available}>
                IA local · Ollama
              </option>
              <option value="openai" disabled={!health?.openai_available}>
                OpenAI · envio de trechos ao provedor
              </option>
            </select>
          </label>
          <button
            className="text-button"
            disabled={
              !connection.token ||
              busy ||
              selectedProvider === health?.llm_provider
            }
            onClick={() => {
              setBusy(true);
              setError("");
              void api
                .selectProvider(selectedProvider)
                .then(() => {
                  onChanged();
                  setSuccess(
                    "Provedor salvo. Novas consultas e lotes usarão essa configuração.",
                  );
                })
                .catch((error) => setError(errorMessage(error)))
                .finally(() => setBusy(false));
            }}
          >
            Salvar provedor de respostas
          </button>
          <dl>
            <div>
              <dt>Provedor de respostas</dt>
              <dd>{health?.llm_provider ?? "Desconectado"}</dd>
            </div>
            <div>
              <dt>Modelo de respostas</dt>
              <dd>
                {health?.query_model || health?.llm_model || "Desconectado"}
              </dd>
            </div>
            <div>
              <dt>Embeddings</dt>
              <dd>{health?.embedding_identity ?? "Desconectado"}</dd>
            </div>
            <div>
              <dt>Envio para IA externa</dt>
              <dd>
                {health?.external_ai_enabled
                  ? "Habilitado no backend"
                  : "Desabilitado"}
              </dd>
            </div>
          </dl>
          <p>
            A escolha acima vale para respostas e extração. Ao ativar a OpenAI
            pelo formulário, os embeddings também passam a usar a OpenAI.
            Índices de provedores diferentes não são misturados.
          </p>
          <p className="fine-print">
            A chave da OpenAI fica somente no backend. O banco local não possui
            criptografia própria; a sessão do WhatsApp é criptografada
            separadamente. Não há coleta da tela nem envio de mensagens.
          </p>
        </details>
      </details>
      <ApiUsage api={api} connected={!!health} />
      <details className="settings-section settings-disclosure">
        <summary>Aparência</summary>
        <label>
          Tema
          <select
            value={theme}
            onChange={(event) => {
              const value = event.target.value;
              setTheme(value);
              localStorage.setItem("memory-theme", value);
              document.documentElement.dataset.theme = value;
            }}
          >
            <option value="system">Acompanhar sistema</option>
            <option value="light">Claro</option>
            <option value="dark">Escuro</option>
          </select>
        </label>
      </details>
      <details className="settings-section settings-disclosure">
        <summary>Acesso por MCP</summary>
        <p>
          Somente leitura. Use um cliente com transporte Streamable HTTP e
          autenticação Bearer.
        </p>
        <code className="endpoint">{connection.url}/mcp/</code>
        <p className="fine-print">
          Envie o token local no cabeçalho Authorization. Não publique esse
          endereço sem HTTPS e controle de rede.
        </p>
      </details>
      <details className="settings-section settings-disclosure">
        <summary>Gerenciar dados</summary>
        <p>
          Excluir uma conversa remove também suas fontes e memórias. Cópias de
          segurança precisam ser gerenciadas separadamente.
        </p>
        {conversations.length === 0 ? (
          <p className="muted">Nenhuma conversa importada.</p>
        ) : (
          conversations.map((chat) => (
            <div className="data-row" key={chat.id}>
              <span>{chat.title}</span>
              <button
                className="icon-button"
                aria-label={`Excluir ${chat.title}`}
                onClick={() => setDeleting(chat.id)}
              >
                <Trash2 size={16} />
              </button>
              {deleting === chat.id && (
                <div className="delete-confirm">
                  <p>
                    Excluir permanentemente todo o histórico de “{chat.title}”
                    deste banco?
                  </p>
                  <button
                    className="danger-button"
                    disabled={busy}
                    onClick={() => void remove(chat.id)}
                  >
                    Excluir conversa e dados
                  </button>
                  <button
                    className="text-button"
                    onClick={() => setDeleting("")}
                  >
                    Cancelar
                  </button>
                </div>
              )}
            </div>
          ))
        )}
        {conversations.length >= 100 && (
          <button
            className="text-button"
            onClick={() =>
              void onMoreConversations().catch((error) =>
                setError(errorMessage(error)),
              )
            }
          >
            Carregar mais conversas
          </button>
        )}
        <button
          className="text-button"
          onClick={() =>
            void onMoreContacts().catch((error) =>
              setError(errorMessage(error)),
            )
          }
        >
          Carregar mais contatos nos filtros
        </button>
      </details>
    </section>
  );
}
