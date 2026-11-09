import { z } from "zod";
import {
  LeaseSchema,
  type Lease,
  type Delivery,
} from "@whatsapp-memory/shared-types/whatsapp";
import { loopbackUrl } from "../config/index.js";

export class BackendError extends Error {
  constructor(readonly status: number) {
    super(`Internal backend unavailable (${status})`);
  }
}
export class Backend {
  readonly url: string;
  constructor(
    url: string,
    private token: string,
    private request: typeof fetch = fetch,
  ) {
    this.url = loopbackUrl(url);
  }
  async post<T>(
    path: string,
    payload: unknown,
    schema: z.ZodType<T>,
  ): Promise<T> {
    const response = await this.request(
      `${this.url}/internal/whatsapp${path}`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${this.token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify(payload),
        redirect: "error",
        signal: AbortSignal.timeout(15000),
      },
    );
    if (!response.ok) throw new BackendError(response.status);
    return schema.parse(await response.json());
  }
  claim(instance_id: string, run_id: string) {
    return this.post("/lease", { instance_id, run_id }, LeaseSchema);
  }
  heartbeat(lease: Lease) {
    return this.post("/lease/heartbeat", lease, LeaseSchema);
  }
  release(lease: Lease) {
    return this.post(
      "/lease/release",
      lease,
      z.object({ released: z.boolean() }),
    );
  }
  status(lease: Lease, state: string, account_id: string | null) {
    return this.post(
      "/connection/status",
      { ...lease, state, account_id },
      z.object({ accepted: z.literal(true) }),
    );
  }
  deliver(lease: Lease, items: Delivery[]) {
    return this.post(
      "/events",
      { ...lease, items },
      z.object({
        accepted: z.number().int().nonnegative(),
        duplicates: z.number().int().nonnegative(),
        ignored: z.number().int().nonnegative(),
        last_sequence: z.number().int().nonnegative(),
      }),
    );
  }
}
