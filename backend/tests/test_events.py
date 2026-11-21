from __future__ import annotations

import asyncio

from app.services.events import EventBus


async def test_publish_reaches_every_subscriber():
    bus = EventBus()
    first = await bus.subscribe("run-1")
    second = await bus.subscribe("run-1")
    await bus.publish("run-1", {"type": "step", "seq": 1})
    assert (await asyncio.wait_for(first.get(), 1))["seq"] == 1
    assert (await asyncio.wait_for(second.get(), 1))["seq"] == 1


async def test_channels_are_isolated():
    bus = EventBus()
    a = await bus.subscribe("a")
    b = await bus.subscribe("b")
    await bus.publish("a", {"type": "step"})
    assert a.qsize() == 1
    assert b.qsize() == 0


async def test_unsubscribe_stops_delivery():
    bus = EventBus()
    queue = await bus.subscribe("run")
    await bus.unsubscribe("run", queue)
    await bus.publish("run", {"type": "step"})
    assert queue.qsize() == 0
    assert bus.subscriber_count("run") == 0


async def test_slow_subscriber_drops_oldest_events():
    bus = EventBus(max_queue=2)
    queue = await bus.subscribe("run")
    for index in range(5):
        await bus.publish("run", {"seq": index})
    drained = [queue.get_nowait()["seq"] for _ in range(queue.qsize())]
    assert drained == [3, 4]


async def test_publish_without_subscribers_is_a_noop():
    bus = EventBus()
    await bus.publish("nobody", {"type": "step"})
    assert bus.subscriber_count("nobody") == 0
