import { RequestSchema } from "../shared/messages";

chrome.runtime.onInstalled.addListener(() => {
  void chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });
});
chrome.runtime.onMessage.addListener(
  (message: unknown, sender, sendResponse) => {
    if (
      sender.id !== chrome.runtime.id ||
      !RequestSchema.safeParse(message).success
    )
      return false;
    sendResponse({
      available: false,
      reason:
        "A conversa ativa não está disponível. Selecione uma conversa importada.",
    });
    return false;
  },
);
