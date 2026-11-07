"""The day monitor: watches the triggers and carries out the lock, in order, idempotently."""

import logging

from coolcandle.domain.guard import LockReason, lock_trigger
from coolcandle.domain.models import ExitReason, Mode
from coolcandle.domain.plan import PlanStatus
from coolcandle.ports.clock import Clock
from coolcandle.services.audit import Audit
from coolcandle.services.bus import EventBus
from coolcandle.services.guard.service import GuardService
from coolcandle.services.orders.service import AlertSink, OrderService
from coolcandle.services.plans.service import PlanService

log = logging.getLogger(__name__)


def exit_reason_for(reason: LockReason) -> ExitReason:
    return ExitReason.SQUARE_OFF if reason is LockReason.SQUARE_OFF else ExitReason.DAY_LOCK


class DayMonitor:
    def __init__(
        self,
        *,
        clock: Clock,
        guard: GuardService,
        orders: OrderService,
        plans: PlanService,
        bus: EventBus,
        audit: Audit,
        alerts: AlertSink,
    ) -> None:
        self.clock = clock
        self.guard = guard
        self.orders = orders
        self.plans = plans
        self.bus = bus
        self.audit = audit
        self.alerts = alerts
        self._last_sent: dict[tuple[str, Mode], str] = {}

    async def check(self, account_id: str, mode: Mode) -> None:
        plan = await self.plans.get(account_id)
        reason = self.guard.lock_reason(account_id, mode)
        if reason is not None:
            # A crash can leave trades open after the lock row: keep finishing the job.
            await self.orders.flatten(account_id, mode, exit_reason_for(reason))
        elif plan is not None and plan.status is PlanStatus.LOCKED:
            snapshot = await self.guard.snapshot(account_id, mode)
            trigger = lock_trigger(plan, snapshot)
            if trigger is not None:
                await self._lock(account_id, mode, trigger)
        await self._publish(account_id, mode)

    async def _lock(self, account_id: str, mode: Mode, reason: LockReason) -> None:
        snapshot = await self.guard.snapshot(account_id, mode)
        first_time = await self.guard.write_lock(account_id, mode, reason, snapshot)
        await self.orders.flatten(account_id, mode, exit_reason_for(reason))
        if first_time:
            now = self.clock.now()
            await self.audit.write(
                "day_locked",
                now,
                data={"reason": reason, "mode": mode, "net": str(snapshot.net_pnl)},
            )
            await self.alerts.alert("Day locked", f"{reason}: net ₹{snapshot.net_pnl}")
            log.info("day locked: %s (%s)", reason, mode)

    async def _publish(self, account_id: str, mode: Mode) -> None:
        view = await self.guard.view(account_id, mode)
        self.bus.publish(
            "day_status",
            {
                "mode": mode,
                "status": view.status.model_dump(mode="json"),
                "snapshot": view.snapshot.model_dump(mode="json"),
                "locked_at": view.locked_at.isoformat() if view.locked_at else None,
                "lock_ends_at": view.lock_ends_at.isoformat() if view.lock_ends_at else None,
            },
        )
