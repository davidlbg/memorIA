import { z } from "zod";

export const SourceSchema = z.object({
  id: z.string().uuid(),
  conversation_id: z.string().uuid(),
  sender_contact_id: z.string().uuid().nullable(),
  sender_name_snapshot: z.string().nullable(),
  content: z.string(),
  message_type: z.string(),
  sent_at: z.string().datetime({ offset: true }),
  origin: z.string().optional(),
  revision: z.number().int().positive().optional(),
  connector_metadata: z.record(z.string(), z.json()).optional(),
  content_updated_at: z
    .string()
    .datetime({ offset: true })
    .nullable()
    .optional(),
  external_message_id: z.string().nullable().optional(),
  from_me: z.boolean().nullable().optional(),
  conversation_title: z.string().nullable().optional(),
  retrieval_role: z.enum(["match", "context"]).optional(),
});
export const MemoryTypeSchema = z.enum([
  "fact",
  "event",
  "intention",
  "commitment",
]);
export const MemorySchema = z.object({
  id: z.string().uuid(),
  conversation_id: z.string().uuid(),
  memory_type: MemoryTypeSchema,
  subject_contact_id: z.string().uuid().nullable(),
  content: z.string(),
  confidence: z.number().min(0).max(1),
  status: z.enum(["active", "outdated", "disputed", "deleted"]),
  observed_at: z.string(),
  evidence_summary: z.string(),
  evidence_basis: z.enum(["explicit", "interpretation"]).optional(),
  supporting_quotes: z
    .array(z.object({ message_id: z.string().uuid(), quote: z.string() }))
    .optional(),
  sources: z.array(SourceSchema),
  context_sources: z.array(SourceSchema).optional(),
});
const UsdSchema = z.string().regex(/^\d+\.\d{6}$/);
export const BudgetStatusSchema = z.object({
  enabled: z.boolean(),
  month: z.string(),
  limit_usd: UsdSchema.nullable(),
  prior_usd: UsdSchema.nullable(),
  recorded_usd: UsdSchema,
  reserved_usd: UsdSchema,
  available_usd: UsdSchema.nullable(),
  query_reservation_usd: UsdSchema.nullable(),
  baseline_known: z.boolean(),
  started_at: z.string().nullable(),
  calls: z.number().int().nonnegative(),
  unconfirmed_calls: z.number().int().nonnegative(),
  by_purpose: z.record(z.string(), UsdSchema),
  pricing_version: z.string(),
});
export type BudgetStatus = z.infer<typeof BudgetStatusSchema>;
export const AnswerSchema = z.object({
  cost: z
    .object({
      estimated_usd: UsdSchema.nullable(),
      calls: z.number().int().nonnegative(),
      unconfirmed_calls: z.number().int().nonnegative(),
    })
    .nullable()
    .optional(),
  response_mode: z.enum(["focused", "overview"]).optional(),
  context_revisions: z
    .array(
      z.object({
        id: z.string().uuid(),
        revision: z.number().int().positive(),
      }),
    )
    .optional(),
  coverage: z
    .array(
      z.object({
        conversation_id: z.string().uuid(),
        available_messages: z.number().int().nonnegative(),
        inspected_messages: z.number().int().nonnegative(),
        complete: z.boolean(),
      }),
    )
    .optional(),
  claims: z.array(
    z.object({
      kind: z.enum([
        "explicit_statement",
        "extracted_memory",
        "interpretation",
      ]),
      text: z.string(),
      section: z.string().nullable().optional(),
      supporting_quotes: z
        .array(z.object({ message_id: z.string().uuid(), quote: z.string() }))
        .optional(),
      quote: z.string(),
      source_message_ids: z.array(z.string().uuid()),
    }),
  ),
  insufficient_evidence: z.boolean(),
  clarification: z.string().nullable().optional(),
  research_steps: z
    .array(
      z.object({
        tool: z.string(),
        results: z.number().int().nonnegative(),
        status: z.enum(["ok", "invalid_request", "limited"]),
      }),
    )
    .optional(),
  research_limited: z.boolean().optional(),
  sources: z.array(SourceSchema),
  context_sources: z.array(SourceSchema).optional(),
  provider: z.string(),
});
export const ConversationSchema = z.object({
  id: z.string().uuid(),
  title: z.string(),
  conversation_type: z.string(),
  imported_at: z.string(),
});
export const ContactSchema = z.object({
  id: z.string().uuid(),
  conversation_id: z.string().uuid(),
  display_name: z.string(),
});
export const JobSchema = z.object({
  id: z.string().uuid(),
  conversation_id: z.string().uuid(),
  filename: z.string(),
  source_kind: z.string().optional(),
  status: z.enum(["pending", "processing", "completed", "failed"]),
  total_messages: z.number(),
  imported_messages: z.number(),
  processed_messages: z.number(),
  attempts: z.number(),
  error_message: z.string().nullable(),
  created_at: z.string(),
  completed_at: z.string().nullable(),
});
export const PreviewSchema = z.object({
  messages: z.array(
    z.object({
      sender: z.string().nullable(),
      content: z.string(),
      sent_at: z.string(),
      message_type: z.string(),
      line: z.number(),
      content_hash: z.string(),
    }),
  ),
  total_messages: z.number(),
  participants: z.array(z.string()),
  formats: z.array(z.string()),
  warnings: z.array(z.string()),
  file_hash: z.string(),
  encoding: z.string(),
});
export const HealthSchema = z.object({
  status: z.literal("ok"),
  llm_provider: z.enum(["mock", "openai", "ollama"]),
  embedding_provider: z.string(),
  embedding_identity: z.string(),
  external_ai_enabled: z.boolean(),
  import_max_bytes: z.number(),
  openai_available: z.boolean(),
  ollama_available: z.boolean().optional(),
  llm_model: z.string().optional(),
  query_model: z.string().optional(),
  local_ai_enabled: z.boolean().optional(),
  openai_setup_available: z.boolean().optional(),
  ai_processing_since: z.string().nullable().optional(),
});
export type Source = z.infer<typeof SourceSchema>;
export type Memory = z.infer<typeof MemorySchema>;
export type Answer = z.infer<typeof AnswerSchema>;
export type Conversation = z.infer<typeof ConversationSchema>;
export type Contact = z.infer<typeof ContactSchema>;
export type Job = z.infer<typeof JobSchema>;
export type Preview = z.infer<typeof PreviewSchema>;
export type Health = z.infer<typeof HealthSchema>;
export type MemoryType = z.infer<typeof MemoryTypeSchema>;
