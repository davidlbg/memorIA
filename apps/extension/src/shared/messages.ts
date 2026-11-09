import { z } from "zod";
export const RequestSchema = z
  .object({ type: z.literal("GET_CONTEXT") })
  .strict();
export const ContextSchema = z.object({
  available: z.literal(false),
  reason: z.string(),
});
