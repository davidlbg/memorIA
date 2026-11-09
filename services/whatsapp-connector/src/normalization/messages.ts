import { createHash } from "node:crypto";
import {
  getContentType,
  jidNormalizedUser,
  normalizeMessageContent,
  proto,
  type WAMessage,
  type WAMessageKey,
  type WAMessageUpdate,
} from "@whiskeysockets/baileys";
import {
  WhatsAppEventSchema,
  type WhatsAppEvent,
} from "@whatsapp-memory/shared-types/whatsapp";

export function accountId(self: string): string {
  return createHash("sha256").update(jidNormalizedUser(self)).digest("hex");
}
function number(
  value: number | { toNumber: () => number } | null | undefined,
): number | null {
  if (value === null || value === undefined) return null;
  const result = typeof value === "number" ? value : value.toNumber();
  return Number.isSafeInteger(result) && result >= 0 ? result : null;
}
function jid(value: string | null | undefined): string | undefined {
  return value ? jidNormalizedUser(value) : undefined;
}
function digest(value: unknown): string {
  const canonical = (item: unknown): unknown => {
    if (Array.isArray(item)) return item.map(canonical);
    if (item !== null && typeof item === "object")
      return Object.fromEntries(
        Object.entries(item)
          .filter(([, v]) => v !== undefined)
          .sort(([a], [b]) => a.localeCompare(b, "en"))
          .map(([k, v]) => [k, canonical(v)]),
      );
    return item;
  };
  return createHash("sha256")
    .update(JSON.stringify(canonical(value)))
    .digest("hex");
}

export function normalize(
  raw: WAMessage,
  self: string,
  origin: WhatsAppEvent["origin"],
  operation: WhatsAppEvent["event_type"] = "message.upsert",
  nativeVersion?: number | null,
): WhatsAppEvent | null {
  const content = normalizeMessageContent(raw.message),
    protocol = content?.protocolMessage;
  if (
    protocol?.type === proto.Message.ProtocolMessage.Type.REVOKE &&
    protocol.key
  ) {
    return revoke(protocol.key, self, origin, number(protocol.timestampMs));
  }
  if (
    protocol?.type === proto.Message.ProtocolMessage.Type.MESSAGE_EDIT &&
    protocol.key &&
    protocol.editedMessage
  ) {
    return normalize(
      { ...raw, key: protocol.key, message: protocol.editedMessage },
      self,
      origin,
      "message.edit",
      number(protocol.timestampMs),
    );
  }
  const key = raw.key,
    chat = jid(key.remoteJid);
  if (
    !chat ||
    !key.id ||
    typeof key.fromMe !== "boolean" ||
    !chat.match(/@(s\.whatsapp\.net|lid|g\.us)$/)
  )
    return null;
  const timestamp = number(raw.messageTimestamp),
    version =
      nativeVersion === undefined
        ? timestamp === null
          ? null
          : timestamp * 1000
        : nativeVersion;
  if (version === null || version > 8640000000000000) return null;
  const type = content ? getContentType(content) : undefined;
  let text = "",
    messageType: WhatsAppEvent["message_type"] = "unsupported";
  const metadata: WhatsAppEvent["metadata"] = { native_type: type ?? "stub" };
  const media =
    content?.imageMessage ??
    content?.audioMessage ??
    content?.videoMessage ??
    content?.documentMessage ??
    content?.stickerMessage;
  if (typeof content?.conversation === "string") {
    text = content.conversation;
    messageType = "text";
  } else if (
    content?.extendedTextMessage?.text !== undefined &&
    content.extendedTextMessage.text !== null
  ) {
    text = content.extendedTextMessage.text;
    messageType = "text";
  } else if (media) {
    messageType = "media_reference";
    if ("caption" in media && typeof media.caption === "string") {
      text = media.caption;
      metadata.caption = media.caption;
    }
    if (media.mimetype) metadata.mimetype = media.mimetype;
    if ("fileName" in media && typeof media.fileName === "string")
      metadata.filename = media.fileName;
    const size = number(media.fileLength);
    if (size !== null) metadata.file_size = size;
    if (media.fileSha256)
      metadata.file_sha256 = Buffer.from(media.fileSha256).toString("base64");
  } else if (content?.locationMessage) {
    const location = content.locationMessage;
    messageType = "location";
    if (
      location.degreesLatitude !== null &&
      location.degreesLatitude !== undefined
    )
      metadata.latitude = location.degreesLatitude;
    if (
      location.degreesLongitude !== null &&
      location.degreesLongitude !== undefined
    )
      metadata.longitude = location.degreesLongitude;
    if (location.name) {
      text = location.name;
      metadata.location_name = location.name;
    }
    if (location.address) metadata.address = location.address;
  } else if (content?.contactMessage || content?.contactsArrayMessage) {
    messageType = "contact";
    metadata.contacts = (
      content.contactsArrayMessage?.contacts ?? [content.contactMessage]
    )
      .filter((item) => item !== null && item !== undefined)
      .map((item) => ({
        ...(item.displayName ? { display_name: item.displayName } : {}),
        ...(item.vcard ? { vcard: item.vcard } : {}),
      }));
    text =
      content.contactMessage?.displayName ??
      content.contactsArrayMessage?.displayName ??
      "";
  } else if (
    raw.messageStubType !== undefined &&
    raw.messageStubType !== null
  ) {
    messageType = "system";
    metadata.stub_type = raw.messageStubType;
  }
  const context =
    content?.extendedTextMessage?.contextInfo ??
    content?.imageMessage?.contextInfo ??
    content?.videoMessage?.contextInfo ??
    content?.documentMessage?.contextInfo;
  if (context?.stanzaId) metadata.quoted_message_id = context.stanzaId;
  if (context?.participant)
    metadata.quoted_participant = jid(context.participant);
  const sender = key.fromMe
    ? jid(self)
    : chat.endsWith("@g.us")
      ? jid(key.participant ?? raw.participant)
      : chat;
  const payload = {
    account_id: accountId(self),
    event_type: operation,
    chat_jid: chat,
    chat_jid_alt: jid(key.remoteJidAlt),
    message_id: key.id,
    sender_jid: sender,
    sender_jid_alt: jid(key.participantAlt),
    sender_name: raw.pushName ?? undefined,
    from_me: key.fromMe,
    sent_at:
      timestamp === null ? undefined : new Date(timestamp * 1000).toISOString(),
    version_ms: version,
    message_type: messageType,
    text,
    origin,
    metadata,
  };
  const result = WhatsAppEventSchema.safeParse({
    ...payload,
    event_id: digest(payload),
  });
  return result.success ? result.data : null;
}

export function revoke(
  key: WAMessageKey,
  self: string,
  origin: WhatsAppEvent["origin"],
  version: number | null = null,
): WhatsAppEvent | null {
  const payload = {
    account_id: accountId(self),
    event_type: "message.revoke",
    chat_jid: jid(key.remoteJid),
    chat_jid_alt: jid(key.remoteJidAlt),
    message_id: key.id,
    from_me: key.fromMe,
    version_ms: version,
    message_type: "system",
    text: "",
    origin,
    metadata: { native_type: "revoke" },
  };
  const parsed = WhatsAppEventSchema.safeParse({
    ...payload,
    event_id: digest(payload),
  });
  return parsed.success ? parsed.data : null;
}

export function update(
  raw: WAMessageUpdate,
  self: string,
): WhatsAppEvent | null {
  if (
    raw.update.message === null ||
    raw.update.messageStubType === proto.WebMessageInfo.StubType.REVOKE
  )
    return revoke(raw.key, self, "whatsapp_realtime");
  if (raw.update.message?.editedMessage?.message)
    return normalize(
      {
        ...raw.update,
        key: raw.key,
        message: raw.update.message.editedMessage.message,
      },
      self,
      "whatsapp_realtime",
      "message.edit",
    );
  if (raw.update.message)
    return normalize(
      { ...raw.update, key: raw.key },
      self,
      "whatsapp_realtime",
    );
  return null;
}
