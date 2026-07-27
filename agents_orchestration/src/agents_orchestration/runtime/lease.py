"""LeaseManager: monotonic-epoch lease claim/renew/expire/release (task 4.2)."""

from __future__ import annotations

from datetime import timedelta

from agents_orchestration.domain.lifecycle import Lease


class LeaseManager:
    """Owns lease lifecycle and the monotonic per-Task epoch."""

    def __init__(self, uow, clock, idgen, *, lease_ttl_seconds: float = 30.0) -> None:
        self.uow = uow
        self.clock = clock
        self.idgen = idgen
        self.ttl = timedelta(seconds=lease_ttl_seconds)

    def _next_epoch(self, task_id: str) -> int:
        existing = self.uow.leases.get(task_id)
        return (existing.epoch + 1) if existing else 1

    def claim(self, *, task_id: str, attempt_id: str, run_id: str) -> Lease:
        epoch = self._next_epoch(task_id)
        now = self.clock.now()
        lease = Lease(
            task_id=task_id,
            attempt_id=attempt_id,
            run_id=run_id,
            epoch=epoch,
            claimed_at=now,
            expires_at=now + self.ttl,
        )
        self.uow.leases.save(lease)  # new claim
        return lease

    def renew(self, lease: Lease) -> Lease:
        now = self.clock.now()
        renewed = lease.renew(now + self.ttl, now)
        self.uow.leases.save(renewed, expected_epoch=lease.epoch)
        return renewed

    def release(self, lease: Lease) -> Lease:
        released = lease.release(self.clock.now())
        self.uow.leases.save(released, expected_epoch=lease.epoch)
        return released

    def expire_stale(self, now) -> list[Lease]:
        expired: list[Lease] = []
        for lease in self.uow.leases.active():
            if lease.is_expired(now):
                dead = lease.expire()
                self.uow.leases.save(dead, expected_epoch=lease.epoch)
                expired.append(dead)
        return expired
