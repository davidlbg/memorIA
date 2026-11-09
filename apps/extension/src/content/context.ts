// Integration boundary only. No content script is injected in v0.1.
// Future permitted integrations must return validated context, never scrape state.
export interface ConversationContext {
  available: boolean;
  importedConversationId?: string;
  reason?: string;
}
