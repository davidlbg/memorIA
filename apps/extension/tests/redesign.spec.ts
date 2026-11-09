import { test, expect } from "@playwright/test";
import { resolve } from "node:path";

const cid = "22222222-2222-4222-8222-222222222222";
const mid = "11111111-1111-4111-8111-111111111111";
const jid = "33333333-3333-4333-8333-333333333333";
const source = {
  id: mid,
  conversation_id: cid,
  sender_contact_id: null,
  sender_name_snapshot: "Helena",
  content:
    "O levantamento do Casarão foi entregue. O cálculo estrutural ainda depende de Bruno.",
  message_type: "text",
  sent_at: "2026-11-05T14:32:00Z",
  revision: 1,
};
const job = {
  id: jid,
  conversation_id: cid,
  filename: "exemplo-sintetico.txt",
  status: "completed",
  total_messages: 1,
  imported_messages: 1,
  processed_messages: 1,
  attempts: 0,
  error_message: null,
  created_at: "2026-11-05T14:32:00Z",
  completed_at: "2026-11-05T14:33:00Z",
};

for (const theme of ["light", "dark"] as const) {
  test(`redesign tasks and all surfaces in ${theme}`, async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    let imported = false;
    let deleted = false;
    await page.addInitScript((theme) => {
      localStorage.setItem("memory-theme", theme);
      Object.defineProperty(window, "chrome", {
        configurable: true,
        value: {
          storage: {
            session: {
              get: async () => ({
                connection: {
                  url: "http://127.0.0.1:8000",
                  token: "synthetic-redesign-only-00000000000000",
                },
              }),
              set: async () => {},
            },
          },
        },
      });
    }, theme);
    await page.route("**/api/v1/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      if (route.request().method() === "DELETE") deleted = true;
      let json: unknown = [];
      if (path.endsWith("/health"))
        json = {
          status: "ok",
          llm_provider: "openai",
          embedding_provider: "openai",
          embedding_identity: "openai:text-embedding-3-small:1024",
          external_ai_enabled: true,
          import_max_bytes: 5000000,
          openai_available: true,
          openai_setup_available: true,
        };
      else if (path.endsWith("/conversations"))
        json = [
          {
            id: cid,
            title: "Helena",
            conversation_type: "direct",
            imported_at: "2026-11-01T12:00:00Z",
          },
        ];
      else if (path.endsWith("/messages/validate"))
        json = [{ id: mid, revision: 1 }];
      else if (path.endsWith("/assistant/query"))
        json = {
          claims: [
            {
              kind: "interpretation",
              text: "Helena é arquiteta e trabalha com restauro de edifícios históricos. Mora em Tiradentes e divide o ateliê com a irmã, Teresa.",
              quote: source.content,
              source_message_ids: [mid],
            },
            {
              kind: "interpretation",
              section: "O que ficou combinado",
              text: source.content,
              quote: source.content,
              source_message_ids: [mid],
            },
          ],
          sources: [source],
          insufficient_evidence: false,
          provider: "openai",
          cost: { estimated_usd: "0.013000", calls: 3, unconfirmed_calls: 0 },
        };
      else if (path.endsWith("/memories"))
        json = [
          {
            id: mid,
            conversation_id: cid,
            memory_type: "commitment",
            subject_contact_id: null,
            content:
              "Helena entrega a revisão após a aprovação do cálculo estrutural.",
            confidence: 0.93,
            status: "active",
            observed_at: source.sent_at,
            evidence_summary: "Compromisso condicionado à aprovação.",
            evidence_basis: "interpretation",
            sources: [source],
          },
        ];
      else if (path.endsWith(`/conversations/${cid}`))
        json = {
          conversation: {
            id: cid,
            title: "Helena",
            conversation_type: "direct",
            imported_at: "2026-11-01T12:00:00Z",
          },
          messages: [source],
        };
      else if (path.endsWith("/imports/preview"))
        json = {
          messages: [
            {
              sender: "Helena",
              content: source.content,
              sent_at: source.sent_at,
              message_type: "text",
              line: 1,
              content_hash: "synthetic",
            },
          ],
          total_messages: 1,
          participants: ["Helena"],
          formats: ["Android"],
          warnings: [],
          file_hash: "synthetic",
          encoding: "utf-8",
        };
      else if (
        path.endsWith("/imports") &&
        route.request().method() === "POST"
      ) {
        imported = true;
        json = job;
      } else if (path.endsWith("/imports")) json = imported ? [job] : [];
      else if (path.endsWith("/settings/usage"))
        json = {
          enabled: true,
          month: "2026-11",
          limit_usd: "50.000000",
          prior_usd: "7.470000",
          recorded_usd: "0.013000",
          reserved_usd: "0.000000",
          available_usd: "42.517000",
          query_reservation_usd: "5.340000",
          baseline_known: true,
          started_at: null,
          calls: 3,
          unconfirmed_calls: 0,
          by_purpose: { queries: "0.013000" },
          pricing_version: "synthetic",
        };
      else if (path.endsWith("/whatsapp/connection"))
        json = {
          available: true,
          network_enabled: true,
          state: "CONNECTED",
          account_id: null,
          qr_data_url: null,
          qr_expires_at: null,
          last_status_at: source.sent_at,
          history_notice:
            "Histórico parcial: somente eventos disponibilizados pelo dispositivo vinculado.",
          error_code: null,
          metrics: {
            received: 24,
            discarded: 0,
            duplicates: 0,
            failures: 0,
            reconnections: 0,
            pending: 0,
            connected_seconds: 600,
            last_event_at: source.sent_at,
            last_synced_at: source.sent_at,
            delivery_latency_ms: 0,
            gap_detected: false,
          },
        };
      await route.fulfill({ json });
    });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");
    await page
      .getByRole("button", { name: "Servidor conectado. Abrir ajustes" })
      .waitFor();
    const capture = async (name: string) => {
      await page.evaluate(async () => {
        await document.fonts.ready;
        document.getAnimations().forEach((a) => a.finish());
      });
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
      const nav = await page.locator("nav").boundingBox();
      expect(nav && nav.y + nav.height).toBeLessThanOrEqual(844);
      await page.screenshot({
        path: resolve("../../.impeccable/review", `${name}-${theme}.png`),
        fullPage: true,
      });
    };
    await capture("empty");
    await page.getByLabel("Consultar", { exact: true }).selectOption(cid);
    await page.getByLabel("Pergunte ao seu histórico").fill("Quem é Helena?");
    await page.getByRole("button", { name: "Enviar pergunta" }).click();
    await expect(
      page.getByRole("heading", { name: "O que ficou combinado" }),
    ).toBeVisible();
    await capture("chat");
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.evaluate(() => {
      const original = Element.prototype.scrollIntoView;
      Element.prototype.scrollIntoView = function (options) {
        document.documentElement.dataset.citationScroll =
          typeof options === "object" ? options.behavior : "unspecified";
        original.call(this, options);
      };
    });
    await page.getByRole("button", { name: "Ver mensagem 1" }).first().click();
    await expect(page.locator("blockquote")).toHaveText(source.content);
    await expect(page.locator("html")).toHaveAttribute(
      "data-citation-scroll",
      "auto",
    );
    expect(
      await page
        .locator(".evidence-list")
        .first()
        .evaluate((element) => getComputedStyle(element).animationName),
    ).toBe("none");
    await capture("evidence");
    await page.getByRole("button", { name: "Explorar mensagens" }).click();
    await expect(
      page.getByRole("heading", { name: "Mensagens originais" }),
    ).toBeVisible();
    await expect(
      page
        .locator(".conversation-explorer")
        .getByText(source.content, { exact: true }),
    ).toBeVisible();
    await capture("explorer");
    await page.getByRole("button", { name: "Memórias", exact: true }).click();
    await expect(
      page.getByText(
        "Helena entrega a revisão após a aprovação do cálculo estrutural.",
      ),
    ).toBeVisible();
    await capture("memories");
    await page.getByText("Filtrar memórias", { exact: true }).click();
    await page
      .getByRole("combobox", { name: "Tipo", exact: true })
      .selectOption("commitment");
    await expect(
      page.getByText("Filtros ativos", { exact: true }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Excluir memória" }).click();
    await expect(
      page.getByRole("button", { name: "Cancelar", exact: true }),
    ).toBeVisible();
    expect(deleted).toBe(false);
    await page.getByRole("button", { name: "Cancelar", exact: true }).click();
    expect(deleted).toBe(false);
    await page.getByRole("button", { name: "Importar", exact: true }).click();
    await capture("import");
    await page.getByLabel("Exportação TXT").setInputFiles({
      name: "exemplo-sintetico.txt",
      mimeType: "text/plain",
      buffer: Buffer.from("05/11/2026, 14:32 - Helena: Texto sintético."),
    });
    await expect(page.getByLabel("Nome da conversa")).toBeVisible();
    await page
      .getByRole("button", { name: "Conferir prévia", exact: true })
      .click();
    await expect(
      page.getByRole("heading", { name: "1 mensagens reconhecidas" }),
    ).toBeVisible();
    expect(imported).toBe(false);
    await page.locator(".preview").scrollIntoViewIfNeeded();
    await capture("import-preview");
    await page.getByRole("button", { name: "Confirmar importação" }).click();
    await expect(page.getByRole("status")).toContainText(
      "1 mensagens novas importadas",
    );
    expect(imported).toBe(true);
    await page.getByRole("button", { name: "Ajustes", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "Ajustes", exact: true }),
    ).toBeVisible();
    await capture("settings");
    await page.getByText("WhatsApp", { exact: true }).click();
    await expect(
      page.getByRole("button", { name: "Desconectar e excluir credenciais" }),
    ).toBeVisible();
    await capture("whatsapp");
    await page.getByText("WhatsApp", { exact: true }).click();
    await page
      .getByText("Inteligência e privacidade", { exact: false })
      .first()
      .click();
    await expect(
      page.getByLabel("Chave de API", { exact: true }),
    ).toHaveAttribute("type", "password");
    await capture("settings-ai");
    for (const width of [300, 480, 1440]) {
      await page.setViewportSize({ width, height: 844 });
      await page.getByRole("button", { name: "Chat", exact: true }).click();
      if (
        await page
          .getByRole("button", { name: "Voltar ao assistente", exact: true })
          .count()
      )
        await page
          .getByRole("button", { name: "Voltar ao assistente", exact: true })
          .click();
      await capture(`chat-${width}`);
      const composer = await page.locator(".composer").boundingBox();
      const nav = await page.locator("nav").boundingBox();
      expect(composer && nav && composer.y + composer.height < nav.y).toBe(
        true,
      );
    }
    expect(errors).toEqual([]);
  });
}
