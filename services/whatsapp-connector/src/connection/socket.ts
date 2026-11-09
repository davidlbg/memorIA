import makeWASocket, {
  DisconnectReason,
  type AuthenticationState,
  type WAMessage,
  type WAMessageKey,
  type WAMessageUpdate,
} from "@whiskeysockets/baileys";
import pino from "pino";

export interface SocketCallbacks {
  connection: (
    event:
      | { kind: "qr"; qr: string }
      | { kind: "connecting" }
      | { kind: "open"; self: string }
      | {
          kind: "close";
          reason: "transient" | "restart" | "logout" | "restricted";
        },
  ) => void;
  credentials: () => void;
  messages: (
    messages: WAMessage[],
    origin: "whatsapp_realtime" | "whatsapp_history" | "whatsapp_reconnect",
  ) => void;
  updates: (updates: WAMessageUpdate[]) => void;
  deleted: (keys: WAMessageKey[]) => void;
  unsupported: () => void;
}
export interface ReadSocket {
  end: () => void;
  logout: () => Promise<void>;
}
export type SocketFactory = (
  auth: AuthenticationState,
  callbacks: SocketCallbacks,
) => ReadSocket;

function reason(
  error: unknown,
): "transient" | "restart" | "logout" | "restricted" {
  let status: number | undefined;
  if (
    error !== null &&
    typeof error === "object" &&
    "output" in error &&
    error.output !== null &&
    typeof error.output === "object" &&
    "statusCode" in error.output &&
    typeof error.output.statusCode === "number"
  )
    status = error.output.statusCode;
  if (status === DisconnectReason.loggedOut) return "logout";
  if (status === DisconnectReason.restartRequired) return "restart";
  if (
    status === DisconnectReason.badSession ||
    status === DisconnectReason.connectionReplaced ||
    status === DisconnectReason.forbidden ||
    status === DisconnectReason.multideviceMismatch ||
    status === 463 ||
    status === 429
  )
    return "restricted";
  return "transient";
}

export const baileysSocket: SocketFactory = (auth, callbacks) => {
  const socket = makeWASocket({
    auth,
    logger: pino({ level: "silent" }),
    markOnlineOnConnect: false,
    syncFullHistory: false,
    shouldSyncHistoryMessage: () => true,
    emitOwnEvents: true,
    shouldIgnoreJid: (jid) =>
      jid.endsWith("@broadcast") || jid.endsWith("@newsletter"),
    getMessage: async () => undefined,
  });
  socket.ev.on("creds.update", callbacks.credentials);
  socket.ev.on("connection.update", (event) => {
    if (event.qr) callbacks.connection({ kind: "qr", qr: event.qr });
    if (event.connection === "connecting")
      callbacks.connection({ kind: "connecting" });
    if (event.connection === "open") {
      const self = socket.user?.id ?? auth.creds.me?.id;
      if (self) callbacks.connection({ kind: "open", self });
      else callbacks.connection({ kind: "close", reason: "restricted" });
    }
    if (event.connection === "close")
      callbacks.connection({
        kind: "close",
        reason: reason(event.lastDisconnect?.error),
      });
  });
  socket.ev.on("messages.upsert", (event) =>
    callbacks.messages(
      event.messages,
      event.type === "notify" && !event.requestId
        ? "whatsapp_realtime"
        : "whatsapp_reconnect",
    ),
  );
  socket.ev.on("messaging-history.set", (event) =>
    callbacks.messages(event.messages, "whatsapp_history"),
  );
  socket.ev.on("messages.update", callbacks.updates);
  socket.ev.on("messages.delete", (event) => {
    if ("keys" in event) callbacks.deleted(event.keys);
    else callbacks.unsupported();
  });
  return {
    end: () => socket.end(undefined),
    logout: async () => {
      await socket.logout();
    },
  };
};
