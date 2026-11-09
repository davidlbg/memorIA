import { z } from "zod";

export const ConnectorStateSchema = z.enum([
  "DISCONNECTED",
  "INITIALIZING",
  "QR_REQUIRED",
  "CONNECTING",
  "CONNECTED",
  "RECONNECTING",
  "LOGGED_OUT",
  "ERROR",
]);
export type ConnectorState = z.infer<typeof ConnectorStateSchema>;
export const AccountSchema = z.string().regex(/^[a-f0-9]{64}$/);
export const JidSchema = z
  .string()
  .max(180)
  .regex(/^\d+(?:-\d+)?@(s\.whatsapp\.net|lid|g\.us)$/);
export const MetadataSchema = z
  .object({
    native_type: z.string().max(100),
    caption: z.string().max(20000).optional(),
    mimetype: z.string().max(200).optional(),
    filename: z.string().max(500).optional(),
    file_size: z
      .number()
      .int()
      .nonnegative()
      .max(Number.MAX_SAFE_INTEGER)
      .optional(),
    file_sha256: z.string().max(128).optional(),
    latitude: z.number().min(-90).max(90).optional(),
    longitude: z.number().min(-180).max(180).optional(),
    location_name: z.string().max(500).optional(),
    address: z.string().max(2000).optional(),
    contacts: z
      .array(
        z
          .object({
            display_name: z.string().max(500).optional(),
            vcard: z.string().max(8000).optional(),
          })
          .strict(),
      )
      .max(20)
      .optional(),
    quoted_message_id: z.string().max(200).optional(),
    quoted_participant: JidSchema.optional(),
    stub_type: z.number().int().optional(),
    stub_parameters: z.array(z.string().max(1000)).max(20).optional(),
  })
  .strict();
export const WhatsAppEventSchema = z
  .object({
    event_id: AccountSchema,
    account_id: AccountSchema,
    event_type: z.enum(["message.upsert", "message.edit", "message.revoke"]),
    chat_jid: JidSchema,
    chat_jid_alt: JidSchema.optional(),
    message_id: z
      .string()
      .min(1)
      .max(200)
      .regex(/^[\x21-\x7e]+$/),
    sender_jid: JidSchema.optional(),
    sender_jid_alt: JidSchema.optional(),
    sender_name: z.string().min(1).max(500).optional(),
    from_me: z.boolean(),
    sent_at: z.string().datetime({ offset: true }).optional(),
    version_ms: z.number().int().min(0).max(253402300799999).nullable(),
    message_type: z.enum([
      "text",
      "media_reference",
      "location",
      "contact",
      "system",
      "unsupported",
    ]),
    text: z.string().max(20000),
    origin: z.enum([
      "whatsapp_realtime",
      "whatsapp_history",
      "whatsapp_reconnect",
    ]),
    metadata: MetadataSchema,
  })
  .strict()
  .superRefine((event, ctx) => {
    if (
      event.event_type === "message.upsert" &&
      (!event.sender_jid || !event.sent_at)
    )
      ctx.addIssue({
        code: "custom",
        message: "Upsert requires sender and original timestamp",
      });
    if (event.event_type !== "message.revoke" && event.version_ms === null)
      ctx.addIssue({ code: "custom", message: "Native version required" });
    if (event.event_type === "message.revoke" && event.text !== "")
      ctx.addIssue({ code: "custom", message: "Revoke contains no text" });
  });
export type WhatsAppEvent = z.infer<typeof WhatsAppEventSchema>;
export const LeaseSchema = z.object({
  run_id: z.string().uuid(),
  fence: z.number().int().positive(),
});
export type Lease = z.infer<typeof LeaseSchema>;
export const DeliverySchema = z
  .object({ sequence: z.number().int().positive(), event: WhatsAppEventSchema })
  .strict();
export type Delivery = z.infer<typeof DeliverySchema>;
export const MetricsSchema = z.object({
  conflicted_messages: z.number().int().nonnegative().optional(),
  captured_total: z.number().int().nonnegative().optional(),
  received: z.number().int().nonnegative(),
  discarded: z.number().int().nonnegative(),
  duplicates: z.number().int().nonnegative(),
  failures: z.number().int().nonnegative(),
  reconnections: z.number().int().nonnegative(),
  pending: z.number().int().nonnegative(),
  connected_seconds: z.number().nonnegative(),
  last_event_at: z.string().nullable(),
  last_synced_at: z.string().nullable(),
  delivery_latency_ms: z.number().nonnegative(),
  gap_detected: z.boolean(),
});
export const ConnectorStatusSchema = z.object({
  credentials_present: z.boolean().nullable().optional(),
  connection_requested: z.boolean().nullable().optional(),
  available: z.boolean(),
  network_enabled: z.boolean(),
  state: ConnectorStateSchema,
  account_id: AccountSchema.nullable(),
  qr_data_url: z
    .string()
    .regex(/^data:image\/png;base64,[A-Za-z0-9+/=]+$/)
    .nullable(),
  qr_expires_at: z.string().nullable(),
  last_status_at: z.string(),
  metrics: MetricsSchema,
  history_notice: z.string(),
  error_code: z.string().nullable(),
});
export type ConnectorStatus = z.infer<typeof ConnectorStatusSchema>;
