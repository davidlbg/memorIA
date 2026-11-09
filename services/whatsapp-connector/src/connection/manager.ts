import { randomUUID } from "node:crypto";
import QRCode from "qrcode";
import type {
  ConnectorStatus,
  Lease,
  WhatsAppEvent,
} from "@whatsapp-memory/shared-types/whatsapp";
import { authentication } from "../authentication/state.js";
import type { Config } from "../config/index.js";
import {
  accountId,
  normalize,
  revoke,
  update,
} from "../normalization/messages.js";
import type { EncryptedVault } from "../persistence/vault.js";
import { Backend, BackendError } from "../transport/backend.js";
import { ConnectionMachine, backoff } from "./machine.js";
import type { ReadSocket, SocketCallbacks, SocketFactory } from "./socket.js";

export class ActionError extends Error {
  constructor(
    message: string,
    readonly code:
      "STATE_CONFLICT" | "OUTBOX_DRAIN_REQUIRED" = "STATE_CONFLICT",
  ) {
    super(message);
  }
}
export class InitializationError extends Error {
  constructor(
    readonly stage: string,
    readonly httpStatus: number | null,
  ) {
    super("Connector initialization failed");
  }
}
export class Connector {
  readonly machine = new ConnectionMachine();
  readonly runId = randomUUID();
  private lease: Lease | null = null;
  private socket: ReadSocket | null = null;
  private generation = 0;
  private retry: ReturnType<typeof setTimeout> | null = null;
  private qr: { value: string; expires: number } | null = null;
  private connectedAt: number | null = null;
  private self: string | null = null;
  private syncedState: string | null = null;
  private attempt = 0;
  private serial: Promise<void> = Promise.resolve();
  private pendingTasks = 0;
  private leaseDeadline = 0;
  private lastHeartbeat = 0;
  private deliveryAt = 0;
  private deliveryAttempt = 0;
  private stopped = false;
  private errorCode: string | null = null;
  private metrics: Omit<
    ConnectorStatus["metrics"],
    "pending" | "connected_seconds" | "gap_detected"
  > = {
    received: 0,
    discarded: 0,
    duplicates: 0,
    failures: 0,
    reconnections: 0,
    last_event_at: null,
    last_synced_at: null,
    delivery_latency_ms: 0,
  };
  constructor(
    readonly config: Config,
    readonly vault: EncryptedVault,
    readonly backend: Backend,
    private factory: SocketFactory,
    private fatal: () => void = () => undefined,
  ) {}

  async initialize(): Promise<void> {
    let stage = "lease_claim";
    try {
      const deadline = Date.now() + 120000;
      let waiting = false;
      for (;;) {
        try {
          this.lease = await this.backend.claim(
            this.vault.instanceId,
            this.runId,
          );
          break;
        } catch (error) {
          if (
            !(error instanceof BackendError) ||
            error.status !== 409 ||
            Date.now() >= deadline
          )
            throw error;
          if (!waiting)
            process.stderr.write(
              "Connector waiting for the previous lease to expire; encrypted session preserved.\n",
            );
          waiting = true;
          await new Promise<void>((resolve) => setTimeout(resolve, 2000));
        }
      }
      this.leaseDeadline = Date.now() + 60000;
      stage = "vault_flush";
      await this.vault.mutate(() => undefined);
      stage = "initial_status";
      await this.publish();
      stage = "session_restore";
      if (this.vault.desired && this.config.CONNECTOR_NETWORK_ENABLED)
        await this.connect();
    } catch (error) {
      throw new InitializationError(
        stage,
        error instanceof BackendError ? error.status : null,
      );
    }
  }
  private async publish(): Promise<void> {
    if (!this.lease) throw new ActionError("Lease unavailable");
    await this.backend.status(
      this.lease,
      this.machine.state,
      this.self ? accountId(this.self) : this.vault.pendingAccount,
    );
    this.syncedState = this.machine.state;
  }
  private task(work: () => Promise<void>): void {
    if (this.pendingTasks >= 256) {
      this.metrics.discarded++;
      this.errorCode = "LOCAL_BACKPRESSURE";
      void this.failClosed();
      return;
    }
    this.pendingTasks++;
    const generation = this.generation;
    const next = this.serial.then(async () => {
      if (generation === this.generation && !this.stopped) await work();
    });
    this.serial = next
      .catch(async () => {
        this.metrics.failures++;
        this.errorCode = "EVENT_OR_STORAGE_FAILURE";
        await this.failClosed();
      })
      .finally(() => {
        this.pendingTasks--;
      });
  }
  private async failClosed(): Promise<void> {
    this.generation++;
    this.qr = null;
    this.connectedAt = null;
    this.socket?.end();
    this.socket = null;
    if (this.retry) clearTimeout(this.retry);
    this.retry = null;
    this.machine.move("ERROR");
    process.stderr.write(
      `Connector capture paused: code=${this.errorCode ?? "UNKNOWN"}; pending=${this.vault.pendingCount}.\n`,
    );
    try {
      await this.vault.mutate((next) => {
        next.gap_detected = true;
      });
    } catch {
      this.errorCode = "STORAGE_UNAVAILABLE";
    }
  }
  private bind(auth: ReturnType<typeof authentication>): SocketCallbacks {
    const generation = this.generation;
    this.self = auth.state.creds.me?.id ?? null;
    const valid = () => generation === this.generation && !this.stopped;
    const events = async (items: (WhatsAppEvent | null)[]) => {
      const accepted = items.filter(
        (item): item is WhatsAppEvent => item !== null,
      );
      this.metrics.received += accepted.length;
      this.metrics.discarded += items.length - accepted.length;
      if (accepted.length) {
        await this.vault.enqueue(accepted);
        this.metrics.last_event_at = new Date().toISOString();
      }
    };
    const callbacks: SocketCallbacks = {
      connection: (event) => {
        if (!valid()) return;
        this.task(async () => {
          if (event.kind === "qr") {
            this.qr = { value: event.qr, expires: Date.now() + 45000 };
            this.machine.move("QR_REQUIRED");
          } else if (event.kind === "connecting") {
            if (this.machine.state !== "QR_REQUIRED")
              this.machine.move("CONNECTING");
          } else if (event.kind === "open") {
            this.self = event.self;
            this.qr = null;
            this.connectedAt = Date.now();
            this.attempt = 0;
            this.machine.move("CONNECTED");
          } else {
            process.stderr.write(
              `Connector socket closed: reason=${event.reason}.\n`,
            );
            this.qr = null;
            this.connectedAt = null;
            if (event.reason === "restart" || event.reason === "transient") {
              // Baileys mutates credentials before notifying listeners. Persist
              // the final paired state and queued Signal keys before changing
              // generations, which intentionally invalidates old callbacks.
              await auth.save();
              await this.vault.flush();
            }
            this.socket?.end();
            this.socket = null;
            if (event.reason === "logout") {
              this.generation++;
              await this.vault.clearCredentials();
              this.machine.move("LOGGED_OUT");
            } else if (event.reason === "restricted") {
              this.errorCode = "SESSION_RESTRICTED";
              await this.vault.mutate((next) => {
                next.desired = false;
              });
              this.generation++;
              this.machine.move("ERROR");
            } else {
              this.generation++;
              this.machine.move("RECONNECTING");
              this.metrics.reconnections++;
              this.retry = setTimeout(
                () => {
                  this.retry = null;
                  void this.connect().catch(() => {
                    this.errorCode = "RECONNECT_FAILED";
                    this.machine.move("ERROR");
                  });
                },
                event.reason === "restart" && this.attempt++ === 0
                  ? 0
                  : backoff(this.attempt++),
              );
            }
          }
          try {
            await this.publish();
          } catch {
            this.syncedState = null;
            this.metrics.failures++;
            this.errorCode = "BACKEND_STATUS_PENDING";
          }
        });
      },
      credentials: () => {
        if (valid()) this.task(auth.save);
      },
      messages: (messages, origin) => {
        if (valid())
          this.task(async () => {
            const self = this.self ?? auth.state.creds.me?.id;
            if (!self) {
              this.metrics.discarded += messages.length;
              throw new Error("Account identity unavailable");
            }
            for (let offset = 0; offset < messages.length; offset += 25)
              await events(
                messages
                  .slice(offset, offset + 25)
                  .map((message) => normalize(message, self, origin)),
              );
          });
      },
      updates: (updates) => {
        if (valid())
          this.task(async () => {
            const self = this.self ?? auth.state.creds.me?.id;
            if (!self) throw new Error("Account identity unavailable");
            await events(updates.map((item) => update(item, self)));
          });
      },
      deleted: (keys) => {
        if (valid())
          this.task(async () => {
            const self = this.self ?? auth.state.creds.me?.id;
            if (!self) throw new Error("Account identity unavailable");
            await events(
              keys.map((key) => revoke(key, self, "whatsapp_realtime")),
            );
          });
      },
      unsupported: () => {
        if (valid()) this.metrics.discarded++;
      },
    };
    this.socket = this.factory(auth.state, callbacks);
    return callbacks;
  }
  async connect(): Promise<void> {
    if (this.stopped || !this.lease || !this.config.CONNECTOR_NETWORK_ENABLED)
      throw new ActionError("Network connection is explicitly disabled");
    if (this.errorCode === "SESSION_RESTRICTED")
      throw new ActionError(
        "Review the session in WhatsApp and explicitly disconnect before pairing again",
      );
    if (
      ["INITIALIZING", "CONNECTING", "QR_REQUIRED", "CONNECTED"].includes(
        this.machine.state,
      )
    )
      return;
    if (!this.vault.credentials && this.vault.pendingCount) {
      this.errorCode = "OUTBOX_DRAIN_REQUIRED";
      throw new ActionError(
        "Drain the previous account outbox before pairing",
        "OUTBOX_DRAIN_REQUIRED",
      );
    }
    if (this.retry) clearTimeout(this.retry);
    this.retry = null;
    this.generation++;
    this.qr = null;
    this.errorCode = null;
    this.machine.move("INITIALIZING");
    let stage = "credentials_restore";
    try {
      const generation = this.generation;
      const auth = authentication(
        this.vault,
        () => generation === this.generation && !this.stopped,
      );
      this.self = auth.state.creds.me?.id ?? null;
      stage = "persist_session_intent";
      await this.vault.mutate((next) => {
        next.desired = true;
      });
      await auth.save();
      stage = "publish_initial_identity";
      await this.publish();
      stage = "open_socket";
      this.bind(auth);
    } catch (error) {
      process.stderr.write(
        `Connector connection initialization failed at ${stage}${error instanceof BackendError ? ` (HTTP ${error.status})` : ""}. No sensitive payload was logged.\n`,
      );
      this.errorCode = "INITIALIZATION_FAILED";
      await this.failClosed();
      throw new ActionError("Connector initialization failed");
    }
  }
  async disconnect(): Promise<void> {
    this.generation++;
    if (this.retry) clearTimeout(this.retry);
    this.retry = null;
    this.qr = null;
    const socket = this.socket;
    this.socket = null;
    this.connectedAt = null;
    this.errorCode = socket ? null : "REMOTE_LOGOUT_UNCONFIRMED";
    try {
      if (socket) await socket.logout();
    } catch {
      this.errorCode = "REMOTE_LOGOUT_UNCONFIRMED";
    } finally {
      socket?.end();
      await this.serial;
      await this.vault.clearCredentials();
      this.self = null;
      this.machine.move("DISCONNECTED");
      await this.publish();
    }
  }
  async tick(): Promise<void> {
    if (!this.lease || this.stopped) return;
    let renewingLease = false;
    try {
      if (Date.now() - this.lastHeartbeat > 15000) {
        renewingLease = true;
        await this.backend.heartbeat(this.lease);
        renewingLease = false;
        this.lastHeartbeat = Date.now();
        this.leaseDeadline = Date.now() + 60000;
      }
      if (this.syncedState !== this.machine.state) await this.publish();
      const batch = this.vault.pendingBatch(25);
      if (batch.length && Date.now() >= this.deliveryAt) {
        const started = Date.now(),
          result = await this.backend.deliver(this.lease, batch);
        if (
          result.accepted + result.duplicates + result.ignored !==
          batch.length
        )
          throw new Error("Invalid backend acknowledgement");
        await this.vault.acknowledge(batch);
        if (
          !this.vault.pendingCount &&
          this.errorCode === "OUTBOX_DRAIN_REQUIRED"
        )
          this.errorCode = null;
        this.metrics.duplicates += result.duplicates;
        this.metrics.delivery_latency_ms = Date.now() - started;
        this.metrics.last_synced_at = new Date().toISOString();
        this.deliveryAttempt = 0;
      }
    } catch (error) {
      this.metrics.failures++;
      this.deliveryAt = Date.now() + backoff(this.deliveryAttempt++);
      if (
        error instanceof BackendError &&
        [400, 403, 409, 422, 507].includes(error.status)
      ) {
        this.errorCode = "BACKEND_REJECTED_EVENT";
        await this.failClosed();
      }
      if (
        Date.now() > this.leaseDeadline ||
        (renewingLease && error instanceof BackendError && error.status === 409)
      ) {
        this.stopped = true;
        this.generation++;
        this.socket?.end();
        this.socket = null;
        this.qr = null;
        this.errorCode = "LEASE_LOST";
        this.machine.move("ERROR");
        this.fatal();
      }
    }
  }
  async status(): Promise<ConnectorStatus> {
    const qr = this.qr && this.qr.expires > Date.now() ? this.qr : null;
    return {
      available: true,
      credentials_present: this.vault.credentials !== null,
      connection_requested: this.vault.desired,
      network_enabled: this.config.CONNECTOR_NETWORK_ENABLED,
      state: this.machine.state,
      account_id: this.self ? accountId(this.self) : null,
      qr_data_url: qr
        ? await QRCode.toDataURL(qr.value, {
            errorCorrectionLevel: "L",
            width: 280,
            margin: 2,
          })
        : null,
      qr_expires_at: qr ? new Date(qr.expires).toISOString() : null,
      last_status_at: this.machine.updatedAt,
      metrics: {
        ...this.metrics,
        pending: this.vault.pendingCount,
        connected_seconds: this.connectedAt
          ? (Date.now() - this.connectedAt) / 1000
          : 0,
        gap_detected: this.vault.gapDetected,
      },
      history_notice:
        "Histórico parcial: somente os eventos entregues pelo dispositivo vinculado. A importação TXT continua disponível; não há comprovação de histórico completo.",
      error_code: this.errorCode,
    };
  }
  async drainTasks(): Promise<void> {
    await this.serial;
  }
  async shutdown(): Promise<void> {
    this.stopped = true;
    this.generation++;
    if (this.retry) clearTimeout(this.retry);
    this.qr = null;
    this.socket?.end();
    this.socket = null;
    await this.serial;
    await this.vault.flush();
    if (this.lease) {
      try {
        await this.backend.release(this.lease);
      } catch {
        this.errorCode = "LEASE_RELEASE_UNCONFIRMED";
      }
    }
  }
}
