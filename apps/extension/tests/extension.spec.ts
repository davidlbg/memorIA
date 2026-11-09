import { chromium, expect, test } from "@playwright/test";
import { resolve } from "node:path";

test("packaged MV3 extension loads its service worker and validated side panel", async () => {
  const extension = resolve("dist");
  const context = await chromium.launchPersistentContext("", {
    channel: "chromium",
    headless: true,
    args: [
      `--disable-extensions-except=${extension}`,
      `--load-extension=${extension}`,
    ],
  });
  try {
    const worker =
      context.serviceWorkers()[0] ??
      (await context.waitForEvent("serviceworker"));
    const id = new URL(worker.url()).host;
    const page = await context.newPage();
    await page.goto(`chrome-extension://${id}/index.html`);
    await expect(
      page.getByRole("heading", {
        name: "Relembre a conversa. Entenda o contexto.",
      }),
    ).toBeVisible();
    const result: unknown = await page.evaluate(async () =>
      chrome.runtime.sendMessage({ type: "GET_CONTEXT" }),
    );
    expect(result).toEqual({
      available: false,
      reason:
        "A conversa ativa não está disponível. Selecione uma conversa importada.",
    });
    const permissions = await page.evaluate(async () =>
      chrome.permissions.getAll(),
    );
    expect(permissions.permissions).toEqual(
      expect.arrayContaining(["sidePanel", "storage"]),
    );
    expect(permissions.permissions).not.toContain("tabs");
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({
      path: "test-results/extension-light.png",
      fullPage: true,
    });
  } finally {
    await context.close();
  }
});
