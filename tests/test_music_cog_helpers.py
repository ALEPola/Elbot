from enum import Enum

from elbot.cogs.music import Music, QueuePaginator
from elbot.music import QueuePaginator as ExportedQueuePaginator


class EndReason(Enum):
    FINISHED = "finished"
    STOPPED = "stopped"


def test_queue_paginator_uses_exported_module_path():
    assert QueuePaginator is ExportedQueuePaginator


def test_normalise_end_reason_handles_enum_and_string():
    assert Music._normalise_end_reason(EndReason.FINISHED) == "FINISHED"
    assert Music._normalise_end_reason("finished") == "FINISHED"
    assert Music._normalise_end_reason("EndReason.STOPPED") == "STOPPED"


def test_parse_seek_position():
    assert Music._parse_seek_position("90") == 90_000
    assert Music._parse_seek_position("1:30") == 90_000
    assert Music._parse_seek_position("1:02:03") == 3_723_000
    assert Music._parse_seek_position("1:90") is None
    assert Music._parse_seek_position("not-a-time") is None


def test_safe_log_value_redacts_signed_urls_and_bounds_text():
    value = "https://media.example.test/playback?token=secret&ip=192.0.2.1"
    assert Music._safe_log_value(value) == "https://media.example.test"
    assert Music._safe_log_value("x" * 200, limit=20) == f"{'x' * 20}..."
