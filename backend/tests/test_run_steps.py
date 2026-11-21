from __future__ import annotations

import asyncio

from app import db
from app.models import ReviewRun, RunStep
from app.services.events import EventBus, get_bus, set_bus
from app.services.steps import DbStepSink, ListStepSink, NullStepSink


async def _make_run() -> ReviewRun:
    async with db.get_sessionmaker()() as session:
        run = ReviewRun(
            repository_id=1,
            pr_number=7,
            pr_title="t",
            head_sha="a" * 40,
            mode="lite",
            status="running",
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        return run


async def test_db_sink_persists_steps_in_order(sqlite_engine):
    run = await _make_run()
    sink = DbStepSink(run.id, db.get_sessionmaker())
    await sink.emit("note", "first", {"a": 1})
    await sink.emit("llm_call", "second", {"b": 2}, tokens=12, duration_ms=34)
    await sink.emit("verdict", "third")

    async with db.get_sessionmaker()() as session:
        rows = (
            await session.execute(
                RunStep.__table__.select().where(RunStep.run_id == run.id).order_by(RunStep.seq)
            )
        ).all()

    assert [row.seq for row in rows] == [1, 2, 3]
    assert [row.kind for row in rows] == ["note", "llm_call", "verdict"]
    assert rows[0].detail == {"a": 1}
    assert rows[1].tokens == 12
    assert rows[1].duration_ms == 34


async def test_db_sink_publishes_to_bus(sqlite_engine):
    run = await _make_run()
    bus = EventBus()
    queue = await bus.subscribe(str(run.id))
    sink = DbStepSink(run.id, db.get_sessionmaker(), bus=bus)
    await sink.emit("note", "hello", {"x": 1})
    event = await asyncio.wait_for(queue.get(), timeout=1)
    assert event["type"] == "step"
    assert event["seq"] == 1
    assert event["title"] == "hello"
    assert event["detail"] == {"x": 1}


async def test_db_sink_survives_a_broken_session(sqlite_engine, monkeypatch):
    run = await _make_run()

    def exploding_factory():
        raise RuntimeError("db down")

    sink = DbStepSink(run.id, exploding_factory)
    step = await sink.emit("note", "still returns")
    assert step.seq == 1
    assert step.title == "still returns"


async def test_start_seq_continues_numbering(sqlite_engine):
    run = await _make_run()
    sink = DbStepSink(run.id, db.get_sessionmaker(), start_seq=10)
    step = await sink.emit("note", "x")
    assert step.seq == 11


async def test_titles_are_truncated(sqlite_engine):
    run = await _make_run()
    sink = DbStepSink(run.id, db.get_sessionmaker())
    step = await sink.emit("note", "x" * 500)
    assert len(step.title) == 256


async def test_null_and_list_sinks():
    null_sink = NullStepSink()
    assert (await null_sink.emit("note", "n")).title == "n"
    list_sink = ListStepSink()
    await list_sink.emit("note", "a")
    await list_sink.emit("note", "b")
    assert [step.seq for step in list_sink.steps] == [1, 2]


async def test_global_bus_is_replaceable():
    original = get_bus()
    try:
        replacement = EventBus()
        set_bus(replacement)
        assert get_bus() is replacement
    finally:
        set_bus(original)
