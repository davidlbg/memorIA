import { useCallback, useEffect, useState } from "react";
import { Link2, RefreshCw, Unplug } from "lucide-react";
import type { ConnectorStatus } from "@whatsapp-memory/shared-types/whatsapp";
import type { Api } from "../services/api";

const labels = {
  DISCONNECTED: "Desconectado",
  INITIALIZING: "Inicializando",
  QR_REQUIRED: "Aguardando leitura do QR Code",
  CONNECTING: "Conectando",
  CONNECTED: "Conectado",
  RECONNECTING: "Reconectando",
  LOGGED_OUT: "Sessão encerrada",
  ERROR: "Atenção necessária",
};
const describe = (error: unknown) =>
  error instanceof Error
    ? error.message
    : "Não foi possível consultar o conector.";

export function WhatsAppConnection({
  api,
  enabled,
}: {
  api: Api;
  enabled: boolean;
}) {
  const [result, setResult] = useState<{
    api: Api;
    status: ConnectorStatus;
  } | null>(null);
  const status = result?.api === api ? result.status : null;
  const setStatus = useCallback(
    (value: ConnectorStatus | null) =>
      setResult(
        value
          ? {
              api,
              status:
                value.qr_expires_at &&
                Date.parse(value.qr_expires_at) <= Date.now()
                  ? { ...value, qr_data_url: null }
                  : value,
            }
          : null,
      ),
    [api],
  );
  const [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [confirm, setConfirm] = useState(false),
    [refresh, setRefresh] = useState(0);
  useEffect(() => {
    if (!status?.qr_expires_at) return;
    const timeout = setTimeout(
      () =>
        setResult((value) =>
          value?.api === api
            ? { ...value, status: { ...value.status, qr_data_url: null } }
            : value,
        ),
      Math.max(0, Date.parse(status.qr_expires_at) - Date.now()),
    );
    return () => clearTimeout(timeout);
  }, [api, status?.qr_expires_at]);
  useEffect(() => {
    if (!enabled) return;
    let active = true,
      pending = false;
    const load = async () => {
      if (pending) return;
      pending = true;
      try {
        const next = await api.whatsappStatus();
        if (active) {
          setStatus(next);
          setError("");
        }
      } catch (error) {
        if (active) {
          setError(describe(error));
          setStatus(null);
        }
      } finally {
        pending = false;
      }
    };
    void load();
    const timer = setInterval(() => void load(), 2000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [api, enabled, refresh, setStatus]);
  async function act(action: "connect" | "disconnect") {
    setBusy(true);
    setError("");
    try {
      setStatus(
        action === "connect"
          ? await api.whatsappConnect()
          : await api.whatsappDisconnect(),
      );
      setConfirm(false);
    } catch (error) {
      setError(describe(error));
    } finally {
      setBusy(false);
    }
  }
  const canConnect =
    !(status?.credentials_present === false && status.metrics.pending > 0) &&
    (!status ||
      ["DISCONNECTED", "LOGGED_OUT", "ERROR"].includes(status.state)) &&
    status?.error_code !== "SESSION_RESTRICTED";
  return (
    <div className="settings-section whatsapp-connection">
      <div className="section-heading">
        <h2>Conectar WhatsApp</h2>
        <Link2 size={18} />
      </div>
      <p>
        Vincule um dispositivo pelo QR Code para capturar novas mensagens.
        Nenhuma mensagem será enviada pelo conector.
      </p>
      {!enabled && (
        <p className="fine-print">
          Configure a conexão com o backend primeiro.
        </p>
      )}
      {error && (
        <div className="notice error" role="alert">
          {error}
          <button
            className="text-button"
            onClick={() => setRefresh((value) => value + 1)}
          >
            <RefreshCw size={14} />
            Consultar novamente
          </button>
        </div>
      )}
      {status && (
        <>
          {status.credentials_present === false &&
            status.metrics.pending > 0 && (
              <p className="notice">
                Concluindo o salvamento de {status.metrics.pending} eventos já
                capturados. O pareamento será liberado quando a fila terminar.
              </p>
            )}
          <p className="connection-state" role="status">
            <strong>{labels[status.state]}</strong>
          </p>
          {!status.network_enabled && (
            <p className="fine-print">
              A conexão externa está desabilitada. Habilite
              CONNECTOR_NETWORK_ENABLED no serviço quando quiser vincular sua
              conta.
            </p>
          )}
          {status.state === "QR_REQUIRED" &&
            (status.qr_data_url && status.qr_expires_at ? (
              <div className="qr-code">
                <img
                  src={status.qr_data_url}
                  alt="QR Code temporário para vincular o WhatsApp"
                  width={250}
                  height={250}
                />
                <p>
                  No WhatsApp do celular, abra Dispositivos conectados →
                  Conectar dispositivo.
                </p>
              </div>
            ) : (
              <p className="fine-print">
                Aguardando um novo QR Code válido. O código anterior expirou.
              </p>
            ))}
          <details className="advanced-settings">
            <summary>Atividade e diagnóstico</summary>
            <dl>
              <div>
                <dt>Eventos recebidos nesta execução</dt>
                <dd>{status.metrics.received}</dd>
              </div>
              {status.metrics.captured_total !== undefined && (
                <div>
                  <dt>Mensagens capturadas (acumulado)</dt>
                  <dd>{status.metrics.captured_total}</dd>
                </div>
              )}
              <div>
                <dt>Aguardando persistência</dt>
                <dd>{status.metrics.pending}</dd>
              </div>
              <div>
                <dt>Última entrega confirmada</dt>
                <dd>
                  {status.metrics.last_synced_at
                    ? new Date(status.metrics.last_synced_at).toLocaleString(
                        "pt-BR",
                      )
                    : "Ainda não confirmada"}
                </dd>
              </div>
              <div>
                <dt>Reconexões</dt>
                <dd>{status.metrics.reconnections}</dd>
              </div>
            </dl>
          </details>
          <p className="fine-print">{status.history_notice}</p>
          {!!status.metrics.conflicted_messages && (
            <p className="notice error">
              {status.metrics.conflicted_messages} mensagem(ns) com revisões que
              não puderam ser ordenadas. O conteúdo ambíguo e suas memórias
              foram removidos das consultas; a captura das demais mensagens
              continua. Uma edição com instante posterior pode restabelecer a
              mensagem.
            </p>
          )}
          {status.metrics.gap_detected && (
            <p className="notice error">
              Houve uma interrupção antes da persistência de algum evento. A
              abrangência do histórico é desconhecida; use TXT para recuperação
              quando necessário.
            </p>
          )}
          {status.error_code && (
            <p className="fine-print">
              Código de diagnóstico: <code>{status.error_code}</code>
            </p>
          )}
        </>
      )}
      {canConnect ? (
        <button
          className="secondary-button"
          disabled={!enabled || busy || !status?.network_enabled}
          onClick={() => void act("connect")}
        >
          <Link2 size={16} />
          {busy ? "Iniciando…" : "Conectar WhatsApp"}
        </button>
      ) : (
        <button
          className="text-button"
          disabled={busy}
          onClick={() => setConfirm(true)}
        >
          <Unplug size={15} />
          Desconectar e excluir credenciais
        </button>
      )}
      {confirm && (
        <div className="delete-confirm">
          <p>
            Encerrar a sessão vinculada e apagar as credenciais locais?
            Mensagens já capturadas permanecem no histórico, e a fila local será
            entregue ao backend.
          </p>
          <button
            className="danger-button"
            disabled={busy}
            onClick={() => void act("disconnect")}
          >
            Confirmar desconexão
          </button>
          <button className="text-button" onClick={() => setConfirm(false)}>
            Cancelar
          </button>
        </div>
      )}
    </div>
  );
}
