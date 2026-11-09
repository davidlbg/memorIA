import { it, expect } from "vitest";
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { randomBytes } from "node:crypto";
import { z } from "zod";

it.skipIf(process.env.CONNECTOR_ENTRYPOINT_TEST !== "true")(
  "keeps compiled entrypoint unready until durable initialization completes",
  async () => {
    const temp = await mkdtemp(join(tmpdir(), "wm-readiness-"));
    const key = join(temp, "fixture.key");
    await writeFile(key, randomBytes(32));
    let unblock: (() => void) | undefined;
    let claimed = false;
    const blocked = new Promise<void>((done) => {
      unblock = done;
    });
    const server = createServer(async (request, response) => {
      const chunks: Buffer[] = [];
      for await (const chunk of request) chunks.push(Buffer.from(chunk));
      const owner = z
        .object({ run_id: z.string() })
        .parse(JSON.parse(Buffer.concat(chunks).toString()));
      if (request.url === "/internal/whatsapp/lease") {
        claimed = true;
        await blocked;
      }
      response.setHeader("Content-Type", "application/json");
      response.end(
        JSON.stringify(
          request.url?.endsWith("/lease")
            ? { run_id: owner.run_id, fence: 1 }
            : { accepted: true, released: true },
        ),
      );
    });
    server.listen(0, "127.0.0.1");
    await once(server, "listening");
    const address = server.address();
    if (!address || typeof address === "string")
      throw new Error("Missing fixture port");
    const secret = "readiness-fixture-only-000000000000000000000";
    const child = spawn(process.execPath, [resolve("dist/index.js")], {
      stdio: "ignore",
      env: {
        ...process.env,
        CONNECTOR_SECRET: secret,
        CONNECTOR_KEY_FILE: key,
        CONNECTOR_VAULT_FILE: join(temp, "session.enc"),
        CONNECTOR_NETWORK_ENABLED: "false",
        CONNECTOR_BACKEND_URL: `http://127.0.0.1:${address.port}`,
      },
    });
    const headers = { Authorization: `Bearer ${secret}` };
    try {
      await expect.poll(() => claimed, { timeout: 10000 }).toBe(true);
      expect(
        (await fetch("http://127.0.0.1:3001/health", { headers })).status,
      ).toBe(503);
      if (!unblock) throw new Error("Missing fixture gate");
      unblock();
      await expect
        .poll(
          async () =>
            (await fetch("http://127.0.0.1:3001/health", { headers })).status,
          { timeout: 10000 },
        )
        .toBe(200);
    } finally {
      unblock?.();
      const exited = once(child, "exit");
      child.kill("SIGTERM");
      await exited;
      server.closeAllConnections();
      server.close();
    }
  },
  30000,
);
