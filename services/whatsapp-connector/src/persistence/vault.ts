import {
  createCipheriv,
  createDecipheriv,
  randomBytes,
  randomUUID,
} from "node:crypto";
import { mkdir, open, readFile, rename, stat, unlink } from "node:fs/promises";
import { dirname } from "node:path";
import { z } from "zod";
import {
  DeliverySchema,
  type Delivery,
} from "@whatsapp-memory/shared-types/whatsapp";

const VaultSchema = z
  .object({
    version: z.literal(1),
    instance_id: z.string().uuid(),
    desired: z.boolean(),
    next_sequence: z.number().int().positive(),
    credentials: z.string().nullable(),
    keys: z.record(z.string(), z.string()),
    outbox: z.array(DeliverySchema),
    gap_detected: z.boolean(),
  })
  .strict();
type VaultData = z.infer<typeof VaultSchema>;
export class EncryptedVault {
  private data: VaultData;
  private serial: Promise<void> = Promise.resolve();
  private constructor(
    private path: string,
    private key: Buffer,
    private maxBytes: number,
    private maxQueue: number,
    data: VaultData,
  ) {
    this.data = data;
  }
  static async open(
    path: string,
    key: Buffer,
    maxBytes = 50000000,
    maxQueue = 10000,
  ): Promise<EncryptedVault> {
    if (key.length !== 32) throw new Error("Invalid encryption key");
    let data: VaultData;
    try {
      if ((await stat(path)).size > maxBytes + 128)
        throw new Error("Vault exceeds configured limit");
      const raw = await readFile(path);
      if (raw.subarray(0, 4).toString() !== "WM02" || raw.length < 32)
        throw new Error("Invalid vault format");
      const decrypt = createDecipheriv("aes-256-gcm", key, raw.subarray(4, 16));
      decrypt.setAAD(Buffer.from("WhatsAppMemory/v0.2"));
      decrypt.setAuthTag(raw.subarray(16, 32));
      data = VaultSchema.parse(
        JSON.parse(
          Buffer.concat([
            decrypt.update(raw.subarray(32)),
            decrypt.final(),
          ]).toString("utf8"),
        ),
      );
    } catch (error) {
      if (!(
        error instanceof Error &&
        "code" in error &&
        error.code === "ENOENT"
      ))
        throw new Error(
          "Cannot open encrypted vault; check key, integrity and storage",
          { cause: error },
        );
      data = {
        version: 1,
        instance_id: randomUUID(),
        desired: false,
        next_sequence: 1,
        credentials: null,
        keys: {},
        outbox: [],
        gap_detected: false,
      };
    }
    return new EncryptedVault(path, key, maxBytes, maxQueue, data);
  }
  get snapshot(): Readonly<VaultData> {
    return structuredClone(this.data);
  }
  get instanceId(): string {
    return this.data.instance_id;
  }
  get desired(): boolean {
    return this.data.desired;
  }
  get credentials(): string | null {
    return this.data.credentials;
  }
  get pendingCount(): number {
    return this.data.outbox.length;
  }
  get gapDetected(): boolean {
    return this.data.gap_detected;
  }
  get pendingAccount(): string | null {
    return this.data.outbox[0]?.event.account_id ?? null;
  }
  keyEntry(id: string): string | undefined {
    return this.data.keys[id];
  }
  pendingBatch(limit: number): Delivery[] {
    return structuredClone(this.data.outbox.slice(0, limit));
  }
  async mutate(update: (next: VaultData) => void): Promise<void> {
    const work = this.serial.then(async () => {
      const next = structuredClone(this.data);
      update(next);
      if (next.outbox.length > this.maxQueue)
        throw new Error("Outbox capacity exceeded");
      const body = Buffer.from(JSON.stringify(next));
      if (body.length > this.maxBytes)
        throw new Error("Encrypted storage capacity exceeded");
      const nonce = randomBytes(12),
        cipher = createCipheriv("aes-256-gcm", this.key, nonce);
      cipher.setAAD(Buffer.from("WhatsAppMemory/v0.2"));
      const encrypted = Buffer.concat([cipher.update(body), cipher.final()]);
      await mkdir(dirname(this.path), { recursive: true, mode: 0o700 });
      const temporary = `${this.path}.${randomUUID()}.tmp`;
      const file = await open(temporary, "wx", 0o600);
      try {
        await file.writeFile(
          Buffer.concat([
            Buffer.from("WM02"),
            nonce,
            cipher.getAuthTag(),
            encrypted,
          ]),
        );
        await file.sync();
      } finally {
        await file.close();
      }
      try {
        await rename(temporary, this.path);
      } catch {
        await unlink(temporary);
        throw new Error("Cannot replace encrypted vault atomically");
      }
      this.data = next;
    });
    this.serial = work.then(
      () => undefined,
      () => undefined,
    );
    return work;
  }
  async enqueue(events: Delivery["event"][]): Promise<void> {
    await this.mutate((next) => {
      for (const event of events)
        next.outbox.push({ sequence: next.next_sequence++, event });
    });
  }
  async acknowledge(deliveries: Delivery[]): Promise<void> {
    await this.mutate((next) => {
      const sequences = new Set(deliveries.map((item) => item.sequence));
      next.outbox = next.outbox.filter((item) => !sequences.has(item.sequence));
    });
  }
  async clearCredentials(): Promise<void> {
    await this.mutate((next) => {
      next.credentials = null;
      next.keys = {};
      next.desired = false;
    });
  }
  async flush(): Promise<void> {
    await this.serial;
  }
}
