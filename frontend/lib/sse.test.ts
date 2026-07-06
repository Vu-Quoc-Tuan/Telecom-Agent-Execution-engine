import assert from "node:assert/strict";
import test from "node:test";

import { consumeSseStream } from "./sse.ts";

test("consumeSseStream releases the reader when event handling throws", async () => {
  let canceled = false;
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode('data: {"ok": true}\n\n'));
    },
    cancel() {
      canceled = true;
    },
  });
  const response = new Response(stream);

  await assert.rejects(
    consumeSseStream(response, () => {
      throw new Error("handler failed");
    }),
    /handler failed/,
  );

  assert.equal(canceled, true);
  assert.equal(stream.locked, false);
});

test("consumeSseStream parses a final buffered event and releases the reader", async () => {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode('event: done\ndata: {"ok": true}'));
      controller.close();
    },
  });
  const response = new Response(stream);
  const events: string[] = [];

  await consumeSseStream(response, (event) => {
    events.push(`${event.event}:${String(event.data.ok)}`);
  });

  assert.deepEqual(events, ["done:true"]);
  assert.equal(stream.locked, false);
});
