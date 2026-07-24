import re
from datetime import UTC, datetime, timedelta

from agents_memory.models import (
    TemporalAnchor,
    TemporalGranularity,
    TemporalResolution,
)


def normalize_temporal_anchor(
    raw_text: str, reference_time: datetime | None
) -> TemporalAnchor:
    text = raw_text.strip()
    absolute = re.fullmatch(
        r"(?P<year>\d{4})(?:-|年)(?P<month>\d{1,2})(?:-|月)"
        r"(?P<day>\d{1,2})日?",
        text,
    )
    if absolute:
        start = datetime(
            int(absolute.group("year")),
            int(absolute.group("month")),
            int(absolute.group("day")),
            tzinfo=UTC,
        )
        return TemporalAnchor(
            raw_text=text,
            start=start,
            end=start + timedelta(days=1),
            granularity=TemporalGranularity.DAY,
            timezone=str(start.tzinfo),
            certainty="exact",
            resolution=TemporalResolution.RESOLVED,
        )
    if reference_time is None:
        return TemporalAnchor(raw_text=text)

    reference = reference_time
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)
    day = reference.replace(hour=0, minute=0, second=0, microsecond=0)
    offsets = {"昨天": -1, "今天": 0, "明天": 1}
    if text in offsets:
        start = day + timedelta(days=offsets[text])
        return TemporalAnchor(
            raw_text=text,
            start=start,
            end=start + timedelta(days=1),
            granularity=TemporalGranularity.DAY,
            timezone=str(start.tzinfo),
            certainty="exact",
            resolution=TemporalResolution.RESOLVED,
        )
    if text == "上周":
        this_week = day - timedelta(days=day.weekday())
        start = this_week - timedelta(days=7)
        return TemporalAnchor(
            raw_text=text,
            start=start,
            end=this_week,
            granularity=TemporalGranularity.WEEK,
            timezone=str(start.tzinfo),
            certainty="exact",
            resolution=TemporalResolution.RESOLVED,
        )
    return TemporalAnchor(raw_text=text)
