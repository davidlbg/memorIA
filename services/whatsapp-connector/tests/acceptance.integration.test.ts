import { z } from "zod";
import { it, expect } from "vitest";
import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { randomBytes, randomUUID } from "node:crypto";
import { Connector } from "../src/connection/manager.js";
import { Backend } from "../src/transport/backend.js";
import { EncryptedVault } from "../src/persistence/vault.js";
import { loadConfig } from "../src/config/index.js";
import type {
  SocketCallbacks,
  SocketFactory,
} from "../src/connection/socket.js";

it.skipIf(!process.env.CONNECTOR_ACCEPTANCE_URL)(
  "durable callback replay reaches real worker and assistant",
  async () => {
    const url = process.env.CONNECTOR_ACCEPTANCE_URL;
    const secret = process.env.CONNECTOR_TEST_SECRET;
    const token = process.env.CONNECTOR_TEST_API_TOKEN;
    if (!url || !secret || !token)
      throw new Error("Explicit isolated fixture configuration required");
    if (!["127.0.0.1", "localhost"].includes(new URL(url).hostname))
      throw new Error("Local fixture only");
    const path = join(
      await mkdtemp(join(tmpdir(), "wm-acceptance-")),
      "vault.enc",
    );
    const key = randomBytes(32);
    const config = loadConfig({
      CONNECTOR_SECRET: secret,
      CONNECTOR_KEY_FILE: "fixture",
      CONNECTOR_NETWORK_ENABLED: "true",
    });
    let callbacks: SocketCallbacks | undefined;
    const factory: SocketFactory = (_auth, cb) => {
      callbacks = cb;
      return { end: () => undefined, logout: async () => undefined };
    };
    const first = new Connector(
      config,
      await EncryptedVault.open(path, key),
      new Backend(url, secret),
      factory,
    );
    const id = "ACCEPTANCE" + randomUUID().replaceAll("-", "");
    const text = "Eu prometo enviar o documento " + id + ".";
    await first.initialize();
    try {
      await first.connect();
      if (!callbacks) throw new Error("Callbacks not initialized");
      callbacks.connection({
        kind: "open",
        self: "5511000000000@s.whatsapp.net",
      });
      await first.drainTasks();
      const message = {
        key: { remoteJid: "5511999999999@s.whatsapp.net", id, fromMe: false },
        message: { conversation: text },
        messageTimestamp: 1793707200,
        pushName: "Synthetic acceptance",
      };
      callbacks.messages([message, message], "whatsapp_realtime");
      await first.drainTasks();
      expect(first.vault.snapshot.outbox.length).toBeGreaterThan(0);
      // Model commit followed by process loss before local ACK: the durable
      // outbox remains intact and must be replayed by the next run.
      const batch = first.vault.snapshot.outbox;
      const owner = await new Backend(url, secret).claim(
        first.vault.snapshot.instance_id,
        first.runId,
      );
      await new Backend(url, secret).deliver(owner, batch);
    } finally {
      await first.shutdown();
    }
    const restored = new Connector(
      config,
      await EncryptedVault.open(path, key),
      new Backend(url, secret),
      factory,
    );
    await restored.initialize();
    try {
      await restored.tick();
      expect(restored.vault.snapshot.outbox).toHaveLength(0);
      const headers = { Authorization: `Bearer ${token}` };
      const read = async (path: string) => {
        const response = await fetch(url + path, { headers });
        expect(response.status).toBe(200);
        return response.text();
      };
      const messages = z
        .array(z.unknown())
        .parse(JSON.parse(await read("/api/v1/messages/search?query=" + id)));
      expect(messages).toHaveLength(1);
      await expect
        .poll(() => read("/api/v1/memories?query=" + id), { timeout: 20000 })
        .toContain(text);
      const answer = await fetch(url + "/api/v1/assistant/query", {
        method: "POST",
        headers: { ...headers, "Content-Type": "application/json" },
        body: JSON.stringify({ question: id }),
      });
      expect(answer.status).toBe(200);
      expect(await answer.text()).toContain(text);
    } finally {
      await restored.shutdown();
    }
  },
  40000,
);
