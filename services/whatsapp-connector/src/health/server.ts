import { createServer, type Server } from "node:http";
import { timingSafeEqual } from "node:crypto";
import { ActionError, type Connector } from "../connection/manager.js";

export function createControlServer(
  token: string,
  getConnector: () => Connector | null,
): Server {
  return createServer(async (request, response) => {
    response.setHeader("Cache-Control", "no-store");
    response.setHeader("X-Content-Type-Options", "nosniff");
    response.setHeader("Content-Type", "application/json");
    const expected = Buffer.from(`Bearer ${token}`),
      provided = Buffer.from(request.headers.authorization ?? "");
    const send = (status: number, payload: unknown) => {
      response.statusCode = status;
      response.end(JSON.stringify(payload));
    };
    if (
      provided.length !== expected.length ||
      !timingSafeEqual(provided, expected)
    ) {
      send(401, { error: "Authentication required" });
      return;
    }
    if (
      request.headers.origin ||
      !["127.0.0.1", "::ffff:127.0.0.1", "::1"].includes(
        request.socket.remoteAddress ?? "",
      )
    ) {
      send(403, { error: "Loopback service requests only" });
      return;
    }
    let bytes = 0;
    for await (const chunk of request) {
      bytes += Buffer.byteLength(chunk);
      if (bytes > 2048) {
        send(413, { error: "Request too large" });
        return;
      }
    }
    const connector = getConnector();
    if (!connector) {
      send(503, { error: "Connector initializing", error_code: "CONNECTOR_INITIALIZING" });
      return;
    }
    try {
      if (
        request.method === "GET" &&
        (request.url === "/health" || request.url === "/connection")
      ) {
        send(200, await connector.status());
        return;
      }
      if (request.method === "POST" && request.url === "/connect") {
        await connector.connect();
        send(200, await connector.status());
        return;
      }
      if (request.method === "POST" && request.url === "/disconnect") {
        await connector.disconnect();
        send(200, await connector.status());
        return;
      }
      send(404, { error: "Unknown route" });
    } catch (error) {
      send(error instanceof ActionError ? 409 : 503, {
        error_code:
          error instanceof ActionError ? error.code : "CONTROL_FAILURE",
        error: "Connector action failed; inspect safe status metrics",
      });
    }
  });
}
