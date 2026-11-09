import { z } from "zod";
import { ConnectorStatusSchema } from "@whatsapp-memory/shared-types/whatsapp";
import {
  AnswerSchema,
  BudgetStatusSchema,
  ContactSchema,
  ConversationSchema,
  HealthSchema,
  JobSchema,
  MemorySchema,
  PreviewSchema,
  SourceSchema,
} from "@whatsapp-memory/shared-types";

export interface Connection {
  url: string;
  token: string;
}
export const ConnectionSchema = z.object({
  url: z.string().url(),
  token: z.string(),
});

export function validateBackendUrl(value: string): string {
  const url = new URL(value);
  if (
    url.username ||
    url.password ||
    url.search ||
    url.hash ||
    url.pathname !== "/"
  )
    throw new Error(
      "Use somente a origem do backend, sem caminho ou credenciais.",
    );
  if (
    url.protocol !== "https:" &&
    !(
      url.protocol === "http:" &&
      ["localhost", "127.0.0.1"].includes(url.hostname)
    )
  )
    throw new Error(
      "HTTP é permitido apenas em localhost. Use HTTPS para outros endereços.",
    );
  return url.origin;
}

export class Api {
  constructor(readonly connection: Connection) {}
  async request<T>(
    path: string,
    schema: z.ZodType<T>,
    init: RequestInit = {},
  ): Promise<T> {
    const url = validateBackendUrl(this.connection.url);
    const headers = new Headers(init.headers);
    headers.set("Authorization", `Bearer ${this.connection.token}`);
    let response: Response;
    try {
      response = await fetch(`${url}/api/v1${path}`, {
        ...init,
        headers,
        credentials: "omit",
        redirect: "error",
        signal: init.signal ?? AbortSignal.timeout(660_000),
      });
    } catch {
      throw new Error(
        "Não foi possível conectar. Verifique se o backend está iniciado e o endereço está correto.",
      );
    }
    const value: unknown = await response.json().catch(() => null);
    if (!response.ok) {
      if (response.status === 401)
        throw new Error(
          "Token de acesso não aceito. Em Ajustes, use somente o valor de AUTH_SECRET do backend configurado, sem o nome da variável. Ambientes diferentes usam tokens diferentes.",
        );
      const error = z
        .object({ error: z.object({ message: z.string() }) })
        .safeParse(value);
      throw new Error(
        error.success
          ? error.data.error.message
          : `O backend respondeu com erro ${response.status}.`,
      );
    }
    const parsed = schema.safeParse(value);
    if (!parsed.success)
      throw new Error(
        "Resposta incompatível do backend. Verifique as versões da extensão e da API.",
      );
    return parsed.data;
  }
  health = () => this.request("/health", HealthSchema);
  usageStatus = () => this.request("/settings/usage", BudgetStatusSchema);
  whatsappStatus = () =>
    this.request("/whatsapp/connection", ConnectorStatusSchema);
  whatsappConnect = () =>
    this.request("/whatsapp/connect", ConnectorStatusSchema, {
      method: "POST",
    });
  whatsappDisconnect = () =>
    this.request("/whatsapp/disconnect", ConnectorStatusSchema, {
      method: "POST",
    });
  conversations = (offset = 0) =>
    this.request(
      `/conversations?limit=100&offset=${offset}`,
      z.array(ConversationSchema),
    );
  conversation = (id: string, beforeId?: string) =>
    this.request(
      `/conversations/${encodeURIComponent(id)}?order=desc&limit=50${beforeId ? `&before_id=${encodeURIComponent(beforeId)}` : ""}`,
      z.object({
        conversation: ConversationSchema,
        messages: z.array(SourceSchema),
      }),
    );
  reprocess = (id: string) =>
    this.request(
      `/conversations/${encodeURIComponent(id)}/reprocess`,
      JobSchema,
      { method: "POST" },
    );
  contacts = (offset = 0) =>
    this.request(
      `/contacts?limit=100&offset=${offset}`,
      z.array(ContactSchema),
    );
  job = (id: string) =>
    this.request(`/imports/${encodeURIComponent(id)}`, JobSchema);
  imports = (offset = 0) =>
    this.request(`/imports?limit=30&offset=${offset}`, z.array(JobSchema));
  memories = (params: URLSearchParams) =>
    this.request(`/memories?${params}`, z.array(MemorySchema));
  search = (params: URLSearchParams) =>
    this.request(`/messages/search?${params}`, z.array(SourceSchema));
  validateSources = async (sources: { id: string; revision: number }[]) => {
    const result: { id: string; revision: number }[] = [];
    for (let offset = 0; offset < sources.length; offset += 500) {
      result.push(
        ...(await this.request(
          "/messages/validate",
          z.array(
            z.object({
              id: z.string().uuid(),
              revision: z.number().int().positive(),
            }),
          ),
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              sources: sources.slice(offset, offset + 500),
            }),
            signal: AbortSignal.timeout(10000),
          },
        )),
      );
    }
    return result;
  };
  query = (
    question: string,
    conversation_id?: string,
    contact_id?: string,
    previous_question?: string,
  ) =>
    this.request("/assistant/query", AnswerSchema, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question,
        conversation_id,
        contact_id,
        previous_question,
        compact_context: true,
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      }),
    });
  preview = (form: FormData) =>
    this.request("/imports/preview", PreviewSchema, {
      method: "POST",
      body: form,
    });
  import = (form: FormData) =>
    this.request("/imports", JobSchema, { method: "POST", body: form });
  retry = (id: string) =>
    this.request(`/imports/${encodeURIComponent(id)}/retry`, JobSchema, {
      method: "POST",
    });
  deleteConversation = (id: string) =>
    this.request(
      `/conversations/${encodeURIComponent(id)}`,
      z.object({ deleted: z.boolean() }),
      { method: "DELETE" },
    );
  deleteMemory = (id: string) =>
    this.request(
      `/memories/${encodeURIComponent(id)}`,
      z.object({ deleted: z.boolean() }),
      { method: "DELETE" },
    );
  selectProvider = (llm_provider: "mock" | "openai" | "ollama") =>
    this.request(
      "/settings/provider",
      z.object({ llm_provider: z.enum(["mock", "openai", "ollama"]) }),
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ llm_provider }),
      },
    );
  configureOpenAI = (api_key: string) =>
    this.request("/settings/openai", HealthSchema, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ api_key, consent: true }),
    });
  removeOpenAI = () =>
    this.request("/settings/openai", HealthSchema, { method: "DELETE" });
}
