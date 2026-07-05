import assert from "node:assert/strict";
import test from "node:test";

import { apiUrl } from "./api.ts";

test("apiUrl normalizes paths against the configured API base URL", () => {
  assert.equal(apiUrl("runs"), "http://127.0.0.1:8000/api/v1/runs");
  assert.equal(apiUrl("/sessions"), "http://127.0.0.1:8000/api/v1/sessions");
});
