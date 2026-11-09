import { it, expect } from "vitest";
import { Backend } from "../src/transport/backend.js";
import { accountId, normalize } from "../src/normalization/messages.js";
import { randomUUID } from "node:crypto";

it.skipIf(!process.env.CONNECTOR_TEST_BACKEND_URL)(
  "delivers a synthetic native event through authenticated HTTP and reads assistant/MCP-compatible evidence",
  async () => {
    const url = process.env.CONNECTOR_TEST_BACKEND_URL,
      internalToken = process.env.CONNECTOR_TEST_SECRET,
      publicToken = process.env.CONNECTOR_TEST_API_TOKEN;
    if (!url || !internalToken || !publicToken)
      throw new Error("Explicit disposable backend configuration required");
    const client = new Backend(url, internalToken),
      owner = await client.claim(randomUUID(), randomUUID());
    try {
      const self = "5511000000000@s.whatsapp.net";
      await client.status(owner, "CONNECTED", accountId(self));
      const native = normalize(
        {
          key: {
            remoteJid: "5511999999999@s.whatsapp.net",
            id: "NODEHTTP" + randomUUID().replaceAll("-", ""),
            fromMe: false,
          },
          message: { conversation: "Eu prometo enviar o documento sintético." },
          messageTimestamp: 1793707200,
          pushName: "Fixture HTTP",
        },
        self,
        "whatsapp_realtime",
      );
      if (!native) throw new Error("Invalid fixture");
      expect(
        (await client.deliver(owner, [{ sequence: 1, event: native }]))
          .accepted,
      ).toBe(1);
      expect(
        (await client.deliver(owner, [{ sequence: 1, event: native }]))
          .duplicates,
      ).toBe(1);
      const response = await fetch(
        url + "/api/v1/messages/search?query=documento",
        { headers: { Authorization: `Bearer ${publicToken}` } },
      );
      expect(response.status).toBe(200);
      expect(await response.text()).toContain(native.message_id);
      const answer = await fetch(url + "/api/v1/assistant/query", {
        method: "POST",
        headers: {
          Authorization: `Bearer ${publicToken}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ question: "documento sintético" }),
      });
      expect(answer.status).toBe(200);
      expect(await answer.text()).toContain(native.text);
    } finally {
      await client.release(owner);
    }
  },
  30000,
);
