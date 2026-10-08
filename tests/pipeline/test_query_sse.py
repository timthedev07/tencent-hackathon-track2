"""Query event payloads must be JSON that the browser can parse."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from app.api.routes.queries import _json_query_events


async def test_query_events_are_json_on_the_sse_wire() -> None:
    progress = {"queryId": "query-1", "message": "Searching connected sources"}
    terminal = {"queryId": "query-1", "errorCode": "NO_SEARCHABLE_CONTENT"}

    async def events() -> AsyncIterator[dict]:
        yield {"event": "query.retrieving", "data": progress}
        yield {"event": "query.denied", "data": terminal}

    encoded = [event.encode().decode("utf-8") async for event in _json_query_events(events())]

    assert len(encoded) == 2
    for wire_event, name, expected in zip(
        encoded,
        ("query.retrieving", "query.denied"),
        (progress, terminal),
        strict=True,
    ):
        assert wire_event.startswith(f"event: {name}\r\n")
        data_line = next(line for line in wire_event.split("\r\n") if line.startswith("data: "))
        assert json.loads(data_line.removeprefix("data: ")) == expected
        assert wire_event.endswith("\r\n\r\n")
