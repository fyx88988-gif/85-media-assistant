import json
import os
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class SupervisorPolicy:
    idle_poll_seconds: float = 15.0
    retry_delays: tuple[float, ...] = (2.0, 5.0, 15.0, 60.0)
    failure_window_seconds: float = 5 * 60.0
    max_failures: int = 6
    circuit_cooldown_seconds: float = 5 * 60.0

    def __post_init__(self) -> None:
        if self.idle_poll_seconds < 0:
            raise ValueError("空闲检查间隔不能小于零。")
        if not self.retry_delays or any(delay < 0 for delay in self.retry_delays):
            raise ValueError("恢复退避间隔无效。")
        if self.failure_window_seconds <= 0 or self.circuit_cooldown_seconds <= 0:
            raise ValueError("熔断时间窗口必须大于零。")
        if self.max_failures <= 0:
            raise ValueError("最大连续失败次数必须大于零。")


@dataclass(frozen=True, slots=True)
class SupervisorState:
    status: str = "idle"
    consecutive_failures: int = 0
    failure_times: tuple[float, ...] = ()
    next_retry_at: float | None = None
    circuit_open_until: float | None = None
    last_error: str | None = None
    updated_at: float | None = None


class SupervisorStateStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> SupervisorState:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return SupervisorState(
                status=str(payload.get("status", "idle")),
                consecutive_failures=max(
                    0, int(payload.get("consecutive_failures", 0))
                ),
                failure_times=tuple(
                    float(value) for value in payload.get("failure_times", ())
                ),
                next_retry_at=_optional_float(payload.get("next_retry_at")),
                circuit_open_until=_optional_float(
                    payload.get("circuit_open_until")
                ),
                last_error=(
                    str(payload["last_error"])
                    if payload.get("last_error") is not None
                    else None
                ),
                updated_at=_optional_float(payload.get("updated_at")),
            )
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return SupervisorState()

    def save(self, state: SupervisorState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(asdict(state), ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temporary, self.path)

    def clear_for_repair(self) -> None:
        self.save(SupervisorState())


class EngineSupervisor:
    """Keep the lightweight local HTTP engine healthy without loading media tools."""

    def __init__(
        self,
        *,
        policy: SupervisorPolicy,
        state_store: SupervisorStateStore,
        probe: Callable[[], bool],
        command: Callable[[], Sequence[str]],
        starter: Callable[[list[str]], Any],
        waiter: Callable[[], bool],
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.policy = policy
        self.state_store = state_store
        self.probe = probe
        self.command = command
        self.starter = starter
        self.waiter = waiter
        self.clock = clock

    def run_cycle(self) -> float:
        now = float(self.clock())
        state = self.state_store.load()

        if self._is_healthy():
            self._record_success(state, now)
            return self.policy.idle_poll_seconds

        if state.circuit_open_until is not None:
            if now < state.circuit_open_until:
                return max(0.0, state.circuit_open_until - now)
            state = SupervisorState()

        if state.next_retry_at is not None and now < state.next_retry_at:
            return max(0.0, state.next_retry_at - now)

        try:
            self.starter(list(self.command()))
            ready = bool(self.waiter())
        except Exception as exc:
            return self._record_failure(state, now, type(exc).__name__)

        if ready:
            self._record_success(state, now)
            return self.policy.idle_poll_seconds
        return self._record_failure(state, now, "engine-not-ready")

    def run(
        self,
        *,
        sleeper: Callable[[float], None] = time.sleep,
        max_cycles: int | None = None,
    ) -> None:
        cycles = 0
        while max_cycles is None or cycles < max_cycles:
            delay = self.run_cycle()
            cycles += 1
            if max_cycles is None or cycles < max_cycles:
                sleeper(delay)

    def _is_healthy(self) -> bool:
        try:
            return bool(self.probe())
        except Exception:
            return False

    def _record_success(self, state: SupervisorState, now: float) -> None:
        if (
            state.status == "healthy"
            and state.consecutive_failures == 0
            and not state.failure_times
            and state.next_retry_at is None
            and state.circuit_open_until is None
            and state.last_error is None
        ):
            return
        self.state_store.save(SupervisorState(status="healthy", updated_at=now))

    def _record_failure(
        self,
        state: SupervisorState,
        now: float,
        reason: str,
    ) -> float:
        oldest = now - self.policy.failure_window_seconds
        failure_times = tuple(
            value for value in state.failure_times if value >= oldest
        ) + (now,)
        failures = len(failure_times)

        if failures >= self.policy.max_failures:
            cooldown = self.policy.circuit_cooldown_seconds
            self.state_store.save(
                SupervisorState(
                    status="circuit-open",
                    consecutive_failures=failures,
                    failure_times=failure_times,
                    next_retry_at=now + cooldown,
                    circuit_open_until=now + cooldown,
                    last_error=reason,
                    updated_at=now,
                )
            )
            return cooldown

        delay = self.policy.retry_delays[
            min(failures - 1, len(self.policy.retry_delays) - 1)
        ]
        self.state_store.save(
            SupervisorState(
                status="recovering",
                consecutive_failures=failures,
                failure_times=failure_times,
                next_retry_at=now + delay,
                circuit_open_until=None,
                last_error=reason,
                updated_at=now,
            )
        )
        return delay


def _optional_float(value: object) -> float | None:
    return None if value is None else float(value)
