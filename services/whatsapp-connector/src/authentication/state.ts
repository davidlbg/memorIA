import {
  BufferJSON,
  initAuthCreds,
  proto,
  type AuthenticationCreds,
  type AuthenticationState,
  type SignalDataTypeMap,
} from "@whiskeysockets/baileys";
import { z } from "zod";
import type { EncryptedVault } from "../persistence/vault.js";

const Bytes = z.instanceof(Uint8Array),
  BufferSchema = z.instanceof(Buffer);
const Pair = z.object({ public: Bytes, private: Bytes });
const Contact = z.object({
  id: z.string(),
  lid: z.string().optional(),
  phoneNumber: z.string().optional(),
  name: z.string().optional(),
  notify: z.string().optional(),
  username: z.string().optional(),
  verifiedName: z.string().optional(),
  imgUrl: z.string().nullable().optional(),
  status: z.string().optional(),
});
const Credentials = z.object({
  noiseKey: Pair,
  pairingEphemeralKeyPair: Pair,
  signedIdentityKey: Pair,
  signedPreKey: z.object({
    keyPair: Pair,
    signature: Bytes,
    keyId: z.number().int(),
    timestampS: z.number().optional(),
  }),
  registrationId: z.number().int(),
  advSecretKey: z.string(),
  me: Contact.optional(),
  account: z
    .record(z.string(), z.unknown())
    .transform((value) => proto.ADVSignedDeviceIdentity.fromObject(value))
    .optional(),
  signalIdentities: z
    .array(
      z.object({
        identifier: z.object({ name: z.string(), deviceId: z.number() }),
        identifierKey: Bytes,
      }),
    )
    .optional(),
  myAppStateKeyId: z.string().optional(),
  firstUnuploadedPreKeyId: z.number().int(),
  nextPreKeyId: z.number().int(),
  lastAccountSyncTimestamp: z.number().optional(),
  platform: z.string().optional(),
  processedHistoryMessages: z.array(
    z.object({
      key: z
        .record(z.string(), z.unknown())
        .transform((value) => proto.MessageKey.fromObject(value)),
      messageTimestamp: z
        .union([
          z.number(),
          z
            .object({
              low: z.number().int(),
              high: z.number().int(),
              unsigned: z.boolean(),
            })
            .transform(
              (value) =>
                proto.WebMessageInfo.fromObject({ messageTimestamp: value })
                  .messageTimestamp,
            ),
        ])
        .nullable()
        .optional(),
    }),
  ),
  accountSyncCounter: z.number().int(),
  accountSettings: z.object({
    unarchiveChats: z.boolean(),
    defaultDisappearingMode: z
      .object({
        ephemeralExpiration: z.number().nullable().optional(),
        ephemeralSettingTimestamp: z
          .union([
            z.number(),
            z
              .object({
                low: z.number(),
                high: z.number(),
                unsigned: z.boolean(),
              })
              .transform(
                (value) =>
                  proto.Conversation.fromObject({
                    ephemeralSettingTimestamp: value,
                  }).ephemeralSettingTimestamp,
              ),
          ])
          .nullable()
          .optional(),
      })
      .optional(),
  }),
  additionalData: z.unknown().optional(),
  registered: z.boolean(),
  pairingCode: z.string().optional(),
  lastPropHash: z.string().optional(),
  routingInfo: BufferSchema.optional(),
});
const decoders: {
  [K in keyof SignalDataTypeMap]: (value: unknown) => SignalDataTypeMap[K];
} = {
  "pre-key": (value) => Pair.parse(value),
  session: (value) => Bytes.parse(value),
  "sender-key": (value) => Bytes.parse(value),
  "sender-key-memory": (value) =>
    z.record(z.string(), z.boolean()).parse(value),
  "app-state-sync-key": (value) =>
    proto.Message.AppStateSyncKeyData.fromObject(
      z.record(z.string(), z.unknown()).parse(value),
    ),
  "app-state-sync-version": (value) =>
    z
      .object({
        version: z.number(),
        hash: BufferSchema,
        indexValueMap: z.record(z.string(), z.object({ valueMac: Bytes })),
      })
      .parse(value),
  "lid-mapping": (value) => z.string().parse(value),
  "device-list": (value) => z.array(z.string()).parse(value),
  tctoken: (value) =>
    z
      .object({
        token: BufferSchema,
        timestamp: z.string().optional(),
        senderTimestamp: z.number().optional(),
      })
      .parse(value),
  "identity-key": (value) => Bytes.parse(value),
};
function decode<T extends keyof SignalDataTypeMap>(
  type: T,
  value: unknown,
): SignalDataTypeMap[T] {
  return decoders[type](value);
}

export function authentication(
  vault: EncryptedVault,
  active: () => boolean = () => true,
): { state: AuthenticationState; save: () => Promise<void> } {
  const encoded = vault.credentials;
  const parsed = encoded
    ? Credentials.parse(JSON.parse(encoded, BufferJSON.reviver))
    : initAuthCreds();
  const creds: AuthenticationCreds = {
    ...parsed,
    pairingCode: parsed.pairingCode,
    lastPropHash: parsed.lastPropHash,
    routingInfo: parsed.routingInfo,
  };
  return {
    state: {
      creds,
      keys: {
        get: async <T extends keyof SignalDataTypeMap>(
          type: T,
          ids: string[],
        ) => {
          const result: { [id: string]: SignalDataTypeMap[T] } = {};
          for (const id of ids) {
            const raw = vault.keyEntry(JSON.stringify([type, id]));
            if (raw !== undefined) {
              const unknownValue: unknown = JSON.parse(raw, BufferJSON.reviver);
              result[id] = decode(type, unknownValue);
            }
          }
          return result;
        },
        set: async (data) => {
          const changes: [string, string | null][] = [];
          for (const [category, records] of Object.entries(data)) {
            for (const [id, value] of Object.entries(records))
              changes.push([
                JSON.stringify([category, id]),
                value === null
                  ? null
                  : JSON.stringify(value, BufferJSON.replacer),
              ]);
          }
          await vault.mutate((next) => {
            if (!active()) throw new Error("Session no longer active");
            for (const [key, value] of changes) {
              if (value === null) delete next.keys[key];
              else next.keys[key] = value;
            }
          });
        },
      },
    },
    save: async () => {
      const raw = JSON.stringify(creds, BufferJSON.replacer);
      await vault.mutate((next) => {
        if (!active()) throw new Error("Session no longer active");
        next.credentials = raw;
      });
    },
  };
}
