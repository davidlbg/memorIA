import type { ConnectorState } from "@whatsapp-memory/shared-types/whatsapp";

const transitions: Record<ConnectorState, ReadonlySet<ConnectorState>> = {
  DISCONNECTED: new Set(["INITIALIZING", "ERROR"]),
  INITIALIZING: new Set([
    "QR_REQUIRED",
    "CONNECTING",
    "CONNECTED",
    "RECONNECTING",
    "DISCONNECTED",
    "LOGGED_OUT",
    "ERROR",
  ]),
  QR_REQUIRED: new Set([
    "CONNECTING",
    "CONNECTED",
    "RECONNECTING",
    "LOGGED_OUT",
    "DISCONNECTED",
    "ERROR",
  ]),
  CONNECTING: new Set([
    "QR_REQUIRED",
    "CONNECTED",
    "RECONNECTING",
    "LOGGED_OUT",
    "DISCONNECTED",
    "ERROR",
  ]),
  CONNECTED: new Set(["RECONNECTING", "LOGGED_OUT", "DISCONNECTED", "ERROR"]),
  RECONNECTING: new Set([
    "INITIALIZING",
    "QR_REQUIRED",
    "CONNECTING",
    "CONNECTED",
    "LOGGED_OUT",
    "DISCONNECTED",
    "ERROR",
  ]),
  LOGGED_OUT: new Set(["INITIALIZING", "DISCONNECTED", "ERROR"]),
  ERROR: new Set(["INITIALIZING", "DISCONNECTED", "LOGGED_OUT"]),
};
export class ConnectionMachine {
  state: ConnectorState = "DISCONNECTED";
  updatedAt = new Date().toISOString();
  move(next: ConnectorState): void {
    if (next !== this.state && !transitions[this.state].has(next))
      throw new Error("Invalid connector state transition");
    this.state = next;
    this.updatedAt = new Date().toISOString();
  }
}
export function backoff(
  attempt: number,
  random: () => number = Math.random,
): number {
  return Math.floor(
    Math.min(60000, 1000 * 2 ** Math.min(attempt, 6)) * (0.5 + random() * 0.5),
  );
}
