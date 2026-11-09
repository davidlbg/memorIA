import { useState } from "react";
import type { Health } from "@whatsapp-memory/shared-types";
import { Api } from "../services/api";
import { errorMessage } from "../sidepanel/App";

export function OpenAISettings({
  api,
  health,
  connected,
  onChanged,
}: {
  api: Api;
  health: Health | null;
  connected: boolean;
  onChanged: () => void;
}) {
  const [key, setKey] = useState("");
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  async function configure(event: React.FormEvent) {
    event.preventDefault();
    if (!consent || busy) return;
    setBusy(true);
    setError("");
    setSuccess("");
    try {
      await api.configureOpenAI(key);
      setConsent(false);
      setSuccess(
        "OpenAI verificada e ativada. Chave criptografada no backend. O histórico anterior não será processado em lote.",
      );
      onChanged();
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setKey("");
      setBusy(false);
    }
  }
  async function remove() {
    setBusy(true);
    setError("");
    setSuccess("");
    try {
      await api.removeOpenAI();
      setKey("");
      setConsent(false);
      setSuccess(
        "Chave removida. Novos envios à OpenAI desativados; modo mock selecionado. Chamadas já em andamento podem concluir.",
      );
      onChanged();
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }
  return (
    <form className="settings-form" onSubmit={(event) => void configure(event)}>
      <h3>Chave de API da OpenAI</h3>
      <p className="fine-print">
        Consultas e novas mensagens usam a OpenAI para respostas, embeddings e
        memórias. O processamento automático não inclui o histórico anterior à
        ativação. Pode haver cobranças na sua conta da API.
      </p>
      <label>
        Chave de API
        <input
          type="password"
          autoComplete="off"
          spellCheck={false}
          value={key}
          onChange={(event) => setKey(event.target.value)}
          required
          minLength={20}
          maxLength={2048}
          placeholder={
            health?.openai_available
              ? "Cole uma nova chave para substituir"
              : "Cole sua chave da OpenAI"
          }
        />
      </label>
      <label className="consent-control">
        <input
          type="checkbox"
          checked={consent}
          onChange={(event) => setConsent(event.target.checked)}
        />
        <span>
          Autorizo enviar os trechos necessários das conversas à OpenAI.
        </span>
      </label>
      <p className="fine-print">
        A chave não fica no armazenamento da extensão e não é exibida após
        salvar. Ela é validada com texto de teste antes da ativação.
      </p>
      {!health?.openai_setup_available && (
        <p className="fine-print">
          Conecte um backend com armazenamento seguro de credenciais habilitado
          para configurar a chave.
        </p>
      )}
      <button
        className="primary-button"
        disabled={
          busy ||
          !connected ||
          !health?.openai_setup_available ||
          !key ||
          !consent
        }
      >
        {busy ? "Verificando…" : "Verificar chave e ativar OpenAI"}
      </button>
      <button
        type="button"
        className="text-button"
        disabled={busy || !connected || !health?.openai_available}
        onClick={() => void remove()}
      >
        Remover chave e desativar OpenAI
      </button>
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
    </form>
  );
}
