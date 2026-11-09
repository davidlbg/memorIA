import { test, expect } from "@playwright/test";

test("side panel navigation, local settings and theme at narrow widths", async ({
  page,
}) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: /Relembre a conversa/ }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Importar", exact: true }).click();
  await expect(page.getByLabel("Exportação TXT")).toBeVisible();
  await page.getByRole("button", { name: "Ajustes", exact: true }).click();
  await page.getByText("Aparência", { exact: true }).click();
  await page.getByLabel("Tema").selectOption("dark");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.screenshot({
    path: "test-results/panel-settings-dark.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 300, height: 700 });
  await expect(page.getByLabel("Token de acesso local")).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.getByRole("button", { name: "Chat", exact: true }).click();
  await page.screenshot({
    path: "test-results/panel-assistant-narrow.png",
    fullPage: true,
  });
});

test("OpenAI native setup uses a masked field and explicit consent at panel widths", async ({
  page,
}) => {
  let active = false;
  const fakeKey = "sk-synthetic-ui-only-not-a-real-key";
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const health = {
      status: "ok",
      llm_provider: active ? "openai" : "mock",
      embedding_provider: active ? "openai" : "mock",
      embedding_identity: active
        ? "openai:text-embedding-3-small:1024"
        : "mock:hash-v1:1024",
      external_ai_enabled: active,
      import_max_bytes: 5000000,
      openai_available: active,
      openai_setup_available: true,
      local_ai_enabled: false,
      llm_model: "gpt-4.1-mini",
      ai_processing_since: active ? "2026-11-05T16:00:00Z" : null,
    };
    if (path.endsWith("/settings/openai")) {
      expect(route.request().postDataJSON()).toEqual({
        api_key: fakeKey,
        consent: true,
      });
      active = true;
      return route.fulfill({
        json: {
          ...health,
          llm_provider: "openai",
          embedding_provider: "openai",
          embedding_identity: "openai:text-embedding-3-small:1024",
          external_ai_enabled: true,
          openai_available: true,
        },
      });
    }
    if (path.endsWith("/health")) return route.fulfill({ json: health });
    if (path.endsWith("/settings/usage"))
      return route.fulfill({
        json: {
          enabled: true,
          month: "2026-11",
          limit_usd: "50.000000",
          prior_usd: "7.470000",
          recorded_usd: "0.002910",
          reserved_usd: "5.340000",
          available_usd: "37.187090",
          query_reservation_usd: "5.340000",
          baseline_known: true,
          started_at: null,
          calls: 2,
          unconfirmed_calls: 1,
          by_purpose: { queries: "0.002910" },
          pricing_version: "synthetic",
        },
      });
    if (path.includes("/whatsapp/"))
      return route.fulfill({
        status: 503,
        json: { error: { message: "Conector sintético indisponível" } },
      });
    return route.fulfill({ json: [] });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Ajustes", exact: true }).click();
  await page
    .getByLabel("Token de acesso local")
    .fill("synthetic-only-token-00000000000000000000");
  await page
    .getByRole("button", { name: "Verificar e salvar conexão" })
    .click();
  await page
    .getByText("Inteligência e privacidade", { exact: false })
    .first()
    .click();
  await page.getByText("Aparência", { exact: true }).click();
  const input = page.getByLabel("Chave de API", { exact: true });
  const button = page.getByRole("button", {
    name: "Verificar chave e ativar OpenAI",
  });
  await expect(input).toHaveAttribute("type", "password");
  await input.fill(fakeKey);
  await expect(button).toBeDisabled();
  await page.getByRole("checkbox").check();
  await expect(button).toBeEnabled();
  await button.click();
  await expect(page.getByText(/OpenAI verificada e ativada/)).toBeVisible();
  await expect(input).toHaveValue("");
  await page.getByText(/Consumo da API/).click();
  await expect(page.getByText(/Reservas não são cobranças/)).toBeVisible();
  for (const [width, theme] of [
    [300, "light"],
    [390, "dark"],
    [480, "light"],
  ] as const) {
    await page.setViewportSize({ width, height: 844 });
    await page.getByLabel("Tema").selectOption(theme);
    const section = page.locator("main");
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await section.screenshot({
      path: `test-results/openai-settings-${width}-${theme}.png`,
    });
  }
});

test("MemorIA keeps the answer readable, originals on demand and composer visible", async ({
  page,
}) => {
  const id = "11111111-1111-4111-8111-111111111111";
  const cid = "22222222-2222-4222-8222-222222222222";
  const source = {
    id,
    conversation_id: cid,
    sender_contact_id: null,
    sender_name_snapshot: "Helena",
    content:
      "Entrada recebida. Restam R$ 30 mil após a aprovação do cálculo estrutural.",
    message_type: "text",
    sent_at: "2026-10-08T12:00:00Z",
    revision: 1,
  };
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/health"))
      return route.fulfill({
        json: {
          status: "ok",
          llm_provider: "openai",
          embedding_provider: "openai",
          embedding_identity: "openai:text-embedding-3-small:1024",
          external_ai_enabled: true,
          import_max_bytes: 5000000,
          openai_available: true,
          openai_setup_available: true,
          llm_model: "gpt-4.1-mini",
          query_model: "gpt-5.4-mini-2026-03-17",
        },
      });
    if (path.endsWith("/conversations"))
      return route.fulfill({
        json: [
          {
            id: cid,
            title: "Helena",
            conversation_type: "direct",
            imported_at: "2026-10-01T12:00:00Z",
          },
        ],
      });
    if (path.endsWith("/messages/validate"))
      return route.fulfill({ json: [{ id, revision: 1 }] });
    if (path.endsWith("/assistant/query")) {
      expect(route.request().postDataJSON().compact_context).toBe(true);
      const broad = route.request().postDataJSON().question.includes("Quem");
      return route.fulfill({
        json: {
          response_mode: broad ? "overview" : "focused",
          claims: broad
            ? [
                {
                  kind: "interpretation",
                  section: "Quem é Helena",
                  text: "Helena é arquiteta e trabalha com restauro de edifícios históricos. Mora em Tiradentes e divide os cuidados do ateliê com a irmã, Teresa.",
                  quote: source.content,
                  source_message_ids: [id],
                },
                {
                  kind: "interpretation",
                  section: "O que ficou combinado",
                  text: "No projeto Casarão, o levantamento foi entregue. O cálculo estrutural segue pendente com Bruno. Restam **R$ 30 mil**, condicionados à aprovação desse cálculo.",
                  quote: source.content,
                  source_message_ids: [id],
                },
              ]
            : [
                {
                  kind: "interpretation",
                  text: "Restam R$ 30 mil, a pagar após a aprovação do cálculo estrutural.",
                  quote: source.content,
                  source_message_ids: [id],
                },
              ],
          insufficient_evidence: false,
          sources: [source],
          provider: "openai",
          coverage: [
            {
              conversation_id: cid,
              available_messages: 654,
              inspected_messages: 654,
              complete: true,
            },
          ],
        },
      });
    }
    return route.fulfill({ json: [] });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Ajustes", exact: true }).click();
  await page
    .getByLabel("Token de acesso local")
    .fill("synthetic-only-token-00000000000000000000");
  await page
    .getByRole("button", { name: "Verificar e salvar conexão" })
    .click();
  await expect(
    page.getByRole("button", { name: "Servidor conectado. Abrir ajustes" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Chat", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Enviar pergunta" }),
  ).toBeDisabled();
  await page.getByLabel("Pergunte ao seu histórico").fill("Quem é Helena?");
  await page.getByLabel("Pergunte ao seu histórico").press("Enter");
  await expect(
    page.getByRole("heading", { name: "O que ficou combinado" }),
  ).toBeVisible();
  await expect(page.locator(".answer-sources")).not.toHaveAttribute("open", "");
  await expect(page.locator(".evidence blockquote")).toHaveCount(0);
  for (const [width, theme] of [
    [300, "light"],
    [390, "dark"],
    [480, "light"],
  ] as const) {
    await page.setViewportSize({ width, height: 844 });
    await page.evaluate(
      (theme) => (document.documentElement.dataset.theme = theme),
      theme,
    );
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    const box = await page.locator(".composer").boundingBox();
    expect(box && box.y + box.height).toBeLessThan(844);
    await page.screenshot({
      path: `test-results/memoria-overview-${width}-${theme}.png`,
    });
  }
  await page.getByRole("button", { name: "Ver mensagem 1" }).first().click();
  await expect(page.locator(".evidence blockquote")).toHaveText(source.content);
  await page.screenshot({ path: "test-results/memoria-original-expanded.png" });
  await page
    .getByLabel("Pergunte ao seu histórico")
    .fill("Qual valor falta pagar?");
  await page.getByRole("button", { name: "Enviar pergunta" }).click();
  await expect(page.locator(".answer").last()).toContainText(
    "Restam R$ 30 mil",
  );
  await expect(page.locator(".answer").last().getByRole("heading")).toHaveCount(
    0,
  );
});
