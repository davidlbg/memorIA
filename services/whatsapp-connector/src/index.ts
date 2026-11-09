import { loadConfig, readEncryptionKey } from "./config/index.js";
import { Connector, InitializationError } from "./connection/manager.js";
import { baileysSocket } from "./connection/socket.js";
import { createControlServer } from "./health/server.js";
import { EncryptedVault } from "./persistence/vault.js";
import { Backend } from "./transport/backend.js";

let startupStage = "configuration";

async function main(): Promise<void> {
  const config = loadConfig();
  let connector: Connector | null = null;
  const server = createControlServer(config.CONNECTOR_SECRET, () => connector);
  startupStage = "control_bind";
  // Binding this fixed loopback port happens BEFORE opening the shared vault.
  // With Compose's shared backend network namespace this is an OS-held single
  // writer lock: a live/stalled process cannot be overtaken just because a lease expired.
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(
      { host: "127.0.0.1", port: config.CONNECTOR_PORT, exclusive: true },
      () => resolve(),
    );
  });
  let initializing: Connector | null = null;
  try {
    startupStage = "encryption_key";
    const key = await readEncryptionKey(config.CONNECTOR_KEY_FILE);
    startupStage = "encrypted_vault";
    const vault = await EncryptedVault.open(
      config.CONNECTOR_VAULT_FILE,
      key,
      config.CONNECTOR_MAX_VAULT_BYTES,
      config.CONNECTOR_MAX_QUEUE,
    );
    initializing = new Connector(
      config,
      vault,
      new Backend(config.CONNECTOR_BACKEND_URL, config.CONNECTOR_SECRET),
      baileysSocket,
      () => process.exit(1),
    );
    startupStage = "lease_and_session";
    await initializing.initialize();
    // Do not expose healthy controls until lease acquisition, durable vault
    // initialization and the first backend status publication have completed.
    connector = initializing;
  } catch (error) {
    if (error instanceof InitializationError)
      process.stderr.write(
        `Connector startup diagnostic: stage=${error.stage}; http=${error.httpStatus ?? "not-http"}.\n`,
      );
    // Failed session restoration must not strand a lease and turn every
    // subsequent startup into another failure for the entire lease duration.
    try {
      await initializing?.shutdown();
    } catch {
      process.stderr.write(
        "Connector startup cleanup failed; encrypted storage was preserved.\n",
      );
    }
    server.close();
    throw new Error(
      "Connector initialization unavailable; check configuration, lease and encrypted volume",
      { cause: error },
    );
  }
  let running = false;
  const timer = setInterval(() => {
    if (running || !connector) return;
    running = true;
    void connector.tick().finally(() => {
      running = false;
    });
  }, 2000);
  const shutdown = () => {
    clearInterval(timer);
    void connector?.shutdown().finally(() => {
      server.close();
      process.exit(0);
    });
  };
  process.once("SIGINT", shutdown);
  process.once("SIGTERM", shutdown);
}
void main().catch(() => {
  process.stderr.write(
    `WhatsApp connector stopped: initialization failed at ${startupStage}. No sensitive payload was logged.\n`,
  );
  process.exit(1);
});
