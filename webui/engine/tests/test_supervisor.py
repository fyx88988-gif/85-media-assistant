from pathlib import Path

from media_assistant.supervisor import (
    EngineSupervisor,
    SupervisorPolicy,
    SupervisorState,
    SupervisorStateStore,
)


def test_healthy_engine_uses_idle_poll_without_starting_heavy_components(
    tmp_path: Path,
) -> None:
    commands_requested = 0
    started: list[list[str]] = []

    def command() -> list[str]:
        nonlocal commands_requested
        commands_requested += 1
        return ["engine"]

    supervisor = EngineSupervisor(
        policy=SupervisorPolicy(idle_poll_seconds=15),
        state_store=SupervisorStateStore(tmp_path / "supervisor.json"),
        probe=lambda: True,
        command=command,
        starter=lambda value: started.append(value),
        waiter=lambda: True,
        clock=lambda: 100.0,
    )

    assert supervisor.run_cycle() == 15
    assert commands_requested == 0
    assert started == []


def test_failed_restarts_follow_bounded_backoff_then_open_the_circuit(
    tmp_path: Path,
) -> None:
    now = 0.0
    policy = SupervisorPolicy(
        retry_delays=(2, 5, 15, 60),
        failure_window_seconds=600,
        max_failures=6,
        circuit_cooldown_seconds=300,
    )
    store = SupervisorStateStore(tmp_path / "supervisor.json")
    supervisor = EngineSupervisor(
        policy=policy,
        state_store=store,
        probe=lambda: False,
        command=lambda: ["engine"],
        starter=lambda _: None,
        waiter=lambda: False,
        clock=lambda: now,
    )

    delays: list[float] = []
    for moment in (0, 2, 7, 22, 82):
        now = float(moment)
        delays.append(supervisor.run_cycle())

    assert delays == [2, 5, 15, 60, 60]

    now = 142
    assert supervisor.run_cycle() == 300
    state = store.load()
    assert state.status == "circuit-open"
    assert state.circuit_open_until == 442

    now = 143
    assert supervisor.run_cycle() == 299

    now = 443
    assert supervisor.run_cycle() == 2


def test_persisted_retry_deadline_prevents_an_immediate_restart(
    tmp_path: Path,
) -> None:
    store = SupervisorStateStore(tmp_path / "supervisor.json")
    store.save(
        SupervisorState(
            status="recovering",
            consecutive_failures=1,
            failure_times=(99.0,),
            next_retry_at=120.0,
            last_error="engine-not-ready",
            updated_at=99.0,
        )
    )
    commands_requested = 0

    def command() -> list[str]:
        nonlocal commands_requested
        commands_requested += 1
        return ["engine"]

    supervisor = EngineSupervisor(
        policy=SupervisorPolicy(),
        state_store=store,
        probe=lambda: False,
        command=command,
        starter=lambda _: None,
        waiter=lambda: False,
        clock=lambda: 100.0,
    )

    assert supervisor.run_cycle() == 20
    assert commands_requested == 0


def test_old_failures_leave_the_failure_window(tmp_path: Path) -> None:
    now = 0.0
    store = SupervisorStateStore(tmp_path / "supervisor.json")
    supervisor = EngineSupervisor(
        policy=SupervisorPolicy(
            retry_delays=(2, 5, 15, 60),
            failure_window_seconds=30,
            max_failures=3,
            circuit_cooldown_seconds=120,
        ),
        state_store=store,
        probe=lambda: False,
        command=lambda: ["engine"],
        starter=lambda _: None,
        waiter=lambda: False,
        clock=lambda: now,
    )

    assert supervisor.run_cycle() == 2
    now = 31
    assert supervisor.run_cycle() == 2
    assert store.load().consecutive_failures == 1


def test_success_and_manual_repair_clear_the_circuit(tmp_path: Path) -> None:
    path = tmp_path / "supervisor.json"
    store = SupervisorStateStore(path)
    store.save(
        SupervisorState(
            status="circuit-open",
            consecutive_failures=6,
            failure_times=(1.0, 2.0, 3.0),
            circuit_open_until=500.0,
            last_error="engine-not-ready",
        )
    )

    store.clear_for_repair()
    repaired = store.load()
    assert repaired == SupervisorState()

    supervisor = EngineSupervisor(
        policy=SupervisorPolicy(),
        state_store=store,
        probe=lambda: True,
        command=lambda: ["engine"],
        starter=lambda _: None,
        waiter=lambda: True,
        clock=lambda: 600.0,
    )
    assert supervisor.run_cycle() == 15
    assert store.load().status == "healthy"


def test_state_store_recovers_from_an_invalid_partial_file(tmp_path: Path) -> None:
    path = tmp_path / "supervisor.json"
    path.write_text("{not-json", encoding="utf-8")

    state = SupervisorStateStore(path).load()

    assert state == SupervisorState()
