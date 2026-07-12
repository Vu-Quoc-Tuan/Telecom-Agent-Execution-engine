import assert from "node:assert/strict";
import test from "node:test";

import {
  isTerminalStreamEvent,
  shouldReplaceAssistantWithStreamError,
} from "./run-status.ts";

test("recognizes terminal SSE events", () => {
  assert.equal(isTerminalStreamEvent("run_completed"), true);
  assert.equal(isTerminalStreamEvent("run_failed"), true);
  assert.equal(isTerminalStreamEvent("error"), true);
  assert.equal(isTerminalStreamEvent("text_delta"), false);
});

test("does not replace a completed answer with a later transport error", () => {
  assert.equal(shouldReplaceAssistantWithStreamError(true), false);
  assert.equal(shouldReplaceAssistantWithStreamError(false), true);
});
