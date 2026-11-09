import { expect, test } from "@playwright/test";

test("real local import preview, duplicate import, indexing, assistant evidence and deletion", async ({
  page,
}) => {
  const backend = process.env.MEMORY_E2E_URL;
  const token = process.env.MEMORY_E2E_TOKEN;
  if (!backend || !token)
    throw new Error(
      "Start the disposable local API + worker and set MEMORY_E2E_URL and MEMORY_E2E_TOKEN.",
    );
  const url = new URL(backend);
  expect(["127.0.0.1", "localhost"]).toContain(url.hostname);
  const exportFile = {
    name: "synthetic-browser-test.txt",
    mimeType: "text/plain",
    buffer: Buffer.from(
      "31/10/2026, 10:00 - Alice: Eu trabalho como engenheira.\n31/10/2026, 10:01 - Alice: Eu prometo enviar o documento sexta.",
    ),
  };
  await page.goto("/");
  await page.getByRole("button", { name: "Ajustes", exact: true }).click();
  await page.getByLabel("Endereço do backend").fill(backend);
  await page.getByLabel("Token de acesso local").fill(token);
  await page
    .getByRole("button", { name: "Verificar e salvar conexão" })
    .click();
  await expect(
    page.getByRole("button", { name: "Servidor conectado. Abrir ajustes" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Importar", exact: true }).click();
  await page.getByLabel("Exportação TXT").setInputFiles(exportFile);
  await page.getByRole("button", { name: "Conferir prévia" }).click();
  await expect(
    page.getByRole("heading", { name: "2 mensagens reconhecidas" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Confirmar importação" }).click();
  await expect(page.getByRole("status")).toContainText(
    "2 mensagens novas importadas. 0 já existentes.",
  );
  await expect(page.getByText("Indexado", { exact: true })).toBeVisible({
    timeout: 20000,
  });
  await page.getByRole("button", { name: "Conferir prévia" }).click();
  await page.getByRole("button", { name: "Confirmar importação" }).click();
  await expect(page.getByRole("status")).toContainText(
    "0 mensagens novas importadas. 2 já existentes.",
  );
  await page.getByRole("button", { name: "Chat", exact: true }).click();
  await page.getByLabel("Pergunte ao seu histórico").fill("documento");
  await page.getByRole("button", { name: "Enviar pergunta" }).click();
  await expect(
    page.getByText("Declaração original", { exact: true }),
  ).toBeVisible();
  await page.getByText("Alice", { exact: true }).click();
  await expect(page.locator("blockquote")).toHaveText(
    "Eu prometo enviar o documento sexta.",
  );
  await page.screenshot({
    path: "test-results/real-assistant-evidence.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "Memórias", exact: true }).click();
  await expect(
    page.getByText("Eu trabalho como engenheira.", { exact: true }).first(),
  ).toBeVisible();
  await page.getByRole("button", { name: "Chat", exact: true }).click();
  await expect(
    page.getByText("Declaração original", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Ajustes", exact: true }).click();
  await page
    .getByRole("button", {
      name: "Excluir synthetic-browser-test",
      exact: true,
    })
    .click();
  await page.getByRole("button", { name: "Excluir conversa e dados" }).click();
  await expect(
    page.getByText("Nenhuma conversa importada.", { exact: true }),
  ).toBeVisible();
});
