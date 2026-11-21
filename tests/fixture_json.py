"""Read JSON emitted by the CLI as fixture documents."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast


def fixture_object(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise AssertionError('Expected a JSON object in the test fixture')
    return cast(dict[str, object], value)


def fixture_objects(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise AssertionError('Expected an array of JSON objects in the test fixture')
    return [fixture_object(item) for item in value]


def parse_fixture(content: str) -> dict[str, object]:
    return fixture_object(json.loads(content))


def read_fixture(path: Path) -> dict[str, object]:
    return parse_fixture(path.read_text(encoding='utf-8'))


def object_at(document: object, *fields: str) -> dict[str, object]:
    for field in fields:
        document = fixture_object(document)[field]
    return fixture_object(document)


def fixture_text(value: object) -> str:
    if not isinstance(value, str):
        raise AssertionError('Expected text in the test fixture')
    return value
