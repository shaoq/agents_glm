from datetime import UTC, datetime

from agents_memory.models import (
    TemporalGranularity,
    TemporalResolution,
)
from agents_memory.processing.temporal import normalize_temporal_anchor


def test_normalizes_relative_day_from_message_time() -> None:
    anchor = normalize_temporal_anchor(
        "明天", datetime(2026, 7, 24, 8, 30, tzinfo=UTC)
    )

    assert anchor.start == datetime(2026, 7, 25, tzinfo=UTC)
    assert anchor.end == datetime(2026, 7, 26, tzinfo=UTC)
    assert anchor.granularity is TemporalGranularity.DAY
    assert anchor.resolution is TemporalResolution.RESOLVED


def test_relative_time_without_reference_remains_unresolved() -> None:
    anchor = normalize_temporal_anchor("明天", None)

    assert anchor.raw_text == "明天"
    assert anchor.start is None
    assert anchor.resolution is TemporalResolution.UNRESOLVED


def test_normalizes_previous_week_from_message_time() -> None:
    anchor = normalize_temporal_anchor(
        "上周", datetime(2026, 7, 24, 8, 30, tzinfo=UTC)
    )

    assert anchor.start == datetime(2026, 7, 13, tzinfo=UTC)
    assert anchor.end == datetime(2026, 7, 20, tzinfo=UTC)
    assert anchor.granularity is TemporalGranularity.WEEK


def test_normalizes_absolute_date_without_message_reference() -> None:
    anchor = normalize_temporal_anchor("2026-07-20", None)

    assert anchor.start == datetime(2026, 7, 20, tzinfo=UTC)
    assert anchor.end == datetime(2026, 7, 21, tzinfo=UTC)
    assert anchor.granularity is TemporalGranularity.DAY
    assert anchor.resolution is TemporalResolution.RESOLVED
