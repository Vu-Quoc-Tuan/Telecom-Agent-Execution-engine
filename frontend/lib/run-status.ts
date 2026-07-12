const TERMINAL_STREAM_EVENTS = new Set(["run_completed", "run_failed", "error"]);

export function isTerminalStreamEvent(eventType: string) {
  return TERMINAL_STREAM_EVENTS.has(eventType);
}

export function shouldReplaceAssistantWithStreamError(terminalEventReceived: boolean) {
  return !terminalEventReceived;
}
