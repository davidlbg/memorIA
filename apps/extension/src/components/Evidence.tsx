import type { Source } from "@whatsapp-memory/shared-types";
import { useState } from "react";
import { FileText, ChevronDown } from "lucide-react";

export const formatDate = (value: string) =>
  new Intl.DateTimeFormat("pt-BR", {
    dateStyle: "short",
    timeStyle: "short",
  }).format(new Date(value));

function OriginalMessage({
  source,
  showContent,
}: {
  source: Source;
  showContent: boolean;
}) {
  const [open, setOpen] = useState(false);
  return (
    <details
      className="evidence"
      data-source-id={source.id}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>
        <FileText size={14} />
        <span>{source.sender_name_snapshot ?? "Mensagem do sistema"}</span>
        <time dateTime={source.sent_at}>{formatDate(source.sent_at)}</time>
        <ChevronDown className="disclosure-chevron" size={14} />
      </summary>
      {open && (
        <>
          {source.conversation_title && (
            <p className="fine-print">{source.conversation_title}</p>
          )}
          {showContent && <blockquote>{source.content}</blockquote>}
          {source.content_updated_at && (
            <p className="fine-print">
              Editada em {formatDate(source.content_updated_at)}.
            </p>
          )}
          <details className="source-details">
            <summary>Detalhes da fonte</summary>
            <p className="fine-print">
              Origem: {source.origin ?? "histórico"} · revisão{" "}
              {source.revision ?? 1}
            </p>
            {source.connector_metadata && (
              <pre className="source-metadata">
                {JSON.stringify(source.connector_metadata, null, 2)}
              </pre>
            )}
            <code>Mensagem {source.id}</code>
          </details>
        </>
      )}
    </details>
  );
}
export function Evidence({
  sources,
  showContent = true,
}: {
  sources: Source[];
  showContent?: boolean;
}) {
  return (
    <div className="evidence-list">
      {sources.map((source) => (
        <OriginalMessage
          key={source.id}
          source={source}
          showContent={showContent}
        />
      ))}
    </div>
  );
}
