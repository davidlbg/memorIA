import { readFile } from "node:fs/promises";
import { z } from "zod";

const ConfigSchema = z.object({
  CONNECTOR_SECRET: z.string().min(32),
  CONNECTOR_KEY_FILE: z.string().min(1),
  CONNECTOR_BACKEND_URL: z.string().default("http://127.0.0.1:8000"),
  CONNECTOR_PORT: z.coerce.number().pipe(z.literal(3001)).default(3001),
  CONNECTOR_VAULT_FILE: z.string().default(".local/whatsapp/session.enc"),
  CONNECTOR_NETWORK_ENABLED: z
    .enum(["true", "false"])
    .default("false")
    .transform((value) => value === "true"),
  CONNECTOR_MAX_QUEUE: z.coerce
    .number()
    .int()
    .min(1)
    .max(100000)
    .default(10000),
  CONNECTOR_MAX_VAULT_BYTES: z.coerce
    .number()
    .int()
    .min(10000)
    .max(200000000)
    .default(50000000),
});
export type Config = z.infer<typeof ConfigSchema>;
export function loopbackUrl(value: string): string {
  const url = new URL(value);
  if (
    url.protocol !== "http:" ||
    !["127.0.0.1", "localhost"].includes(url.hostname) ||
    url.username ||
    url.password ||
    url.search ||
    url.hash ||
    url.pathname !== "/"
  )
    throw new Error("Connector transport requires a loopback HTTP origin");
  return url.origin;
}
export function loadConfig(env: NodeJS.ProcessEnv = process.env): Config {
  const parsed = ConfigSchema.safeParse(env);
  if (!parsed.success)
    throw new Error(
      "Invalid connector configuration; configure secret, encryption key file and limits",
    );
  return {
    ...parsed.data,
    CONNECTOR_BACKEND_URL: loopbackUrl(parsed.data.CONNECTOR_BACKEND_URL),
  };
}
export async function readEncryptionKey(path: string): Promise<Buffer> {
  const key = await readFile(path);
  if (key.length !== 32)
    throw new Error("Connector key file must contain exactly 32 random bytes");
  return key;
}
