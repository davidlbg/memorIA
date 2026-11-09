import { describe, expect, it, vi } from "vitest";
import { Api, validateBackendUrl } from "./api";

describe("authenticated API boundary", () => {
  it("validates the endpoint before sending a token", () => {
    expect(validateBackendUrl("http://127.0.0.1:8000")).toBe(
      "http://127.0.0.1:8000",
    );
    for (const value of [
      "http://example.com",
      "https://name:password@example.com",
      "https://example.com/path",
      "javascript:alert(1)",
    ])
      expect(() => validateBackendUrl(value)).toThrow();
  });
  it("authenticates requests and refuses malformed responses", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ status: "fake" }), { status: 200 }),
      );
    vi.stubGlobal("fetch", fetcher);
    await expect(
      new Api({ url: "http://localhost:8000", token: "local-token" }).health(),
    ).rejects.toThrow("Resposta incompatível");
    const request: RequestInit = fetcher.mock.calls[0][1];
    expect(new Headers(request.headers).get("Authorization")).toBe(
      "Bearer local-token",
    );
    expect(request.redirect).toBe("error");
  });
  it("reports backend and network failures without inventing results", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response(
            JSON.stringify({ error: { message: "Autenticação necessária" } }),
            { status: 401 },
          ),
        ),
    );
    const api = new Api({ url: "http://localhost:8000", token: "wrong" });
    await expect(api.health()).rejects.toThrow("Token de acesso não aceito");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new TypeError("Failed to fetch")),
    );
    await expect(api.health()).rejects.toThrow("Não foi possível conectar");
  });
});

describe("research source revalidation", () => {
  it("validates a long research history in bounded requests without dropping sources", async () => {
    const sources = Array.from({ length: 601 }, (_, index) => ({
      id: `00000000-0000-4000-8000-${index.toString().padStart(12, "0")}`,
      revision: 1,
    }));
    const fetcher = vi.fn(async (_url: string, init: RequestInit) => {
      const body = JSON.parse(String(init.body));
      expect(body.sources.length).toBeLessThanOrEqual(500);
      return new Response(JSON.stringify(body.sources), { status: 200 });
    });
    vi.stubGlobal("fetch", fetcher);
    const result = await new Api({
      url: "http://localhost:8000",
      token: "fixture",
    }).validateSources(sources);
    expect(result).toEqual(sources);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
});
