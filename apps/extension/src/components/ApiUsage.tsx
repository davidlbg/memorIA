import { useEffect, useState } from "react";
import type { BudgetStatus } from "@whatsapp-memory/shared-types";
import type { Api } from "../services/api";

export function usd(value: string, digits = 2): string {
  const amount = Number(value);
  if (amount > 0 && amount < 10 ** -digits)
    return `< US$ ${new Intl.NumberFormat("pt-BR", { minimumFractionDigits: digits }).format(10 ** -digits)}`;
  return new Intl.NumberFormat("pt-BR", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(amount);
}

const purposes: Record<string, string> = {
  queries: "Perguntas",
  memories: "Novas memórias",
  indexing: "Índice de busca",
  setup: "Configuração",
};

export function ApiUsage({ api, connected }: { api: Api; connected: boolean }) {
  const [result, setResult] = useState<{
    api: Api;
    data?: BudgetStatus;
    failed?: boolean;
  } | null>(null);
  useEffect(() => {
    if (!connected) return;
    let active = true,
      pending = false;
    const refresh = async () => {
      if (pending) return;
      pending = true;
      try {
        const data = await api.usageStatus();
        if (active) setResult({ api, data });
      } catch {
        if (active) setResult({ api, failed: true });
      } finally {
        pending = false;
      }
    };
    void refresh();
    const timer = setInterval(() => void refresh(), 30_000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [api, connected]);
  if (!connected) return null;
  const current = result?.api === api ? result : null;
  if (current?.failed)
    return (
      <p className="fine-print">
        Não foi possível atualizar o consumo da API. Os valores não estão
        disponíveis.
      </p>
    );
  const data = current?.data;
  if (!data) return <p className="fine-print">Consultando consumo da API…</p>;
  if (!data.enabled)
    return (
      <p className="fine-print">
        Limite mensal da API não configurado neste backend.
      </p>
    );
  return (
    <details className="settings-section settings-disclosure api-usage">
      <summary>
        Consumo da API ·{" "}
        {data.limit_usd && data.prior_usd !== null
          ? `${usd((Number(data.prior_usd) + Number(data.recorded_usd)).toFixed(6))} / ${usd(data.limit_usd)}`
          : "total desconhecido"}
      </summary>
      <p className="fine-print">
        Orçamento conjunto de todas as pessoas neste backend. Mês {data.month},
        em UTC.
      </p>
      <dl className="status-list">
        <div>
          <dt>Gasto anterior informado</dt>
          <dd>
            {data.prior_usd === null ? "Desconhecido" : usd(data.prior_usd)}
          </dd>
        </div>
        <div>
          <dt>Medido desde a ativação</dt>
          <dd>{usd(data.recorded_usd, 4)}</dd>
        </div>
        <div>
          <dt>Reservas temporárias</dt>
          <dd>{usd(data.reserved_usd, 4)}</dd>
        </div>
        <div>
          <dt>Disponível estimado</dt>
          <dd>
            {data.baseline_known && data.available_usd !== null
              ? usd(data.available_usd)
              : "Desconhecido"}
          </dd>
        </div>
        {Object.entries(data.by_purpose).map(([purpose, value]) => (
          <div key={purpose}>
            <dt>{purposes[purpose] ?? purpose}</dt>
            <dd>{usd(value, 4)}</dd>
          </div>
        ))}
      </dl>
      <p className="fine-print">
        Reservas não são cobranças: cada chamada precisa de margem antes de
        começar. O saldo não utilizado é liberado quando a API confirma o uso.{" "}
        {data.query_reservation_usd &&
          `Uma etapa de pesquisa pode reservar até ${usd(data.query_reservation_usd)}.`}
      </p>
      {data.unconfirmed_calls > 0 && (
        <p className="notice">
          Há {data.unconfirmed_calls} chamada(s) sem confirmação de cobrança. A
          reserva continua retida para evitar ultrapassar o orçamento.
        </p>
      )}
      <p className="fine-print">
        Estimativa pelas tarifas registradas. A fatura da OpenAI é a referência
        final. Uso desta chave fora deste backend não entra nesta medição. A
        captura de mensagens continua ao atingir o limite.
      </p>
    </details>
  );
}
