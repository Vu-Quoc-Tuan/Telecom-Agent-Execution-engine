from __future__ import annotations

import asyncio
import unittest


class BackgroundStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_forwards_events_until_the_producer_finishes(self) -> None:
        from app.streaming.background import shielded_stream

        async def events():
            yield "run_started", {"run_id": "run-1"}
            yield "run_completed", {"run_id": "run-1", "final_answer": "done"}

        received = [event async for event in shielded_stream(events())]

        self.assertEqual(
            [
                ("run_started", {"run_id": "run-1"}),
                ("run_completed", {"run_id": "run-1", "final_answer": "done"}),
            ],
            received,
        )

    async def test_disconnected_subscriber_does_not_cancel_or_orphan_the_producer(self) -> None:
        from app.streaming.background import _BACKGROUND_TASKS, shielded_stream

        release_producer = asyncio.Event()
        producer_finished = asyncio.Event()

        async def events():
            yield "run_started", {"run_id": "run-1"}
            await release_producer.wait()
            producer_finished.set()
            yield "run_completed", {"run_id": "run-1", "final_answer": "done"}

        stream = shielded_stream(events())
        first_event = await anext(stream)
        self.assertEqual("run_started", first_event[0])

        await stream.aclose()
        await asyncio.sleep(0)

        self.assertEqual(1, len(_BACKGROUND_TASKS))
        release_producer.set()
        await asyncio.wait_for(producer_finished.wait(), timeout=1)
        await asyncio.sleep(0)

        self.assertEqual(0, len(_BACKGROUND_TASKS))

    async def test_cancelling_the_sse_consumer_does_not_cancel_the_producer(self) -> None:
        from app.streaming.background import _BACKGROUND_TASKS, shielded_stream

        first_event_sent = asyncio.Event()
        release_producer = asyncio.Event()
        producer_finished = asyncio.Event()

        async def events():
            yield "run_started", {"run_id": "run-2"}
            await release_producer.wait()
            producer_finished.set()
            yield "run_completed", {"run_id": "run-2", "final_answer": "done"}

        async def consume() -> None:
            async for _event in shielded_stream(events()):
                first_event_sent.set()

        consumer = asyncio.create_task(consume())
        await asyncio.wait_for(first_event_sent.wait(), timeout=1)
        consumer.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await consumer

        self.assertEqual(1, len(_BACKGROUND_TASKS))
        release_producer.set()
        await asyncio.wait_for(producer_finished.wait(), timeout=1)
        await asyncio.sleep(0)

        self.assertEqual(0, len(_BACKGROUND_TASKS))


if __name__ == "__main__":
    unittest.main()
