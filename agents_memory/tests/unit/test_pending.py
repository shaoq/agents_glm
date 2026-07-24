from datetime import UTC, datetime, timedelta

from agents_memory.processing.pending import PendingResolutionPolicy


def test_pending_policy_uses_value_tier_ttls() -> None:
    now = datetime(2026, 7, 24, tzinfo=UTC)
    policy = PendingResolutionPolicy(
        high_days=30,
        normal_days=7,
        low_days=1,
    )

    assert policy.expires_at(8, now=now) == now + timedelta(days=30)
    assert policy.expires_at(5, now=now) == now + timedelta(days=7)
    assert policy.expires_at(4, now=now) == now + timedelta(days=1)
