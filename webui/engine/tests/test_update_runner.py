from pathlib import Path

import pytest

import media_assistant.update_runner as update_runner
from media_assistant.update_runner import UpdateRunLock, UpdateStatusStore
from media_assistant.update_security import UpdateSecurityError
from media_assistant.updater import ActivationResult, UpdateOfflineError


class OfflineCoordinator:
    def check_and_stage(self) -> None:
        raise UpdateOfflineError("offline")


class SecurityFailureCoordinator:
    def check_and_stage(self) -> None:
        raise UpdateSecurityError("bad signature")


def test_update_runner_returns_zero_when_source_is_offline(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        update_runner,
        "build_coordinator",
        lambda data_root: OfflineCoordinator(),
    )

    exit_code = update_runner.run(["--check"], data_root=tmp_path)
    status = UpdateStatusStore(tmp_path / "updates" / "status.json").read()

    assert exit_code == 0
    assert status.state == "offline"
    assert status.message == "暂时无法检查更新，正在使用当前版本。"


def test_update_runner_returns_three_for_a_security_rejection(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        update_runner,
        "build_coordinator",
        lambda data_root: SecurityFailureCoordinator(),
    )

    exit_code = update_runner.run(["--check"], data_root=tmp_path)
    status = UpdateStatusStore(tmp_path / "updates" / "status.json").read()

    assert exit_code == 3
    assert status.state == "security-error"
    assert "安全校验" in status.message


def test_update_status_store_rejects_unknown_state(tmp_path: Path) -> None:
    path = tmp_path / "status.json"
    path.write_text('{"state":"mystery"}', encoding="utf-8")

    with pytest.raises(ValueError, match="更新状态格式无效"):
        UpdateStatusStore(path).read()


def test_update_runner_applies_a_staged_descriptor_after_waiting_for_engine(
    tmp_path: Path,
    monkeypatch,
) -> None:
    descriptor = tmp_path / "updates" / "staged.json"
    descriptor.parent.mkdir(parents=True)
    descriptor.write_text('{"version":"1.2.0","artifacts":[]}', encoding="utf-8")

    class ApplyCoordinator:
        def __init__(self) -> None:
            self.calls: list[tuple[Path, int | None]] = []

        def apply_staged(self, path: Path, wait_pid: int | None) -> ActivationResult:
            self.calls.append((path, wait_pid))
            return ActivationResult(activated=True)

    coordinator = ApplyCoordinator()
    monkeypatch.setattr(update_runner, "build_coordinator", lambda _: coordinator)

    exit_code = update_runner.run(
        ["--apply", str(descriptor), "--wait-pid", "4321"],
        data_root=tmp_path,
    )
    status = UpdateStatusStore(tmp_path / "updates" / "status.json").read()

    assert exit_code == 0
    assert coordinator.calls == [(descriptor, 4321)]
    assert status.state == "current"
    assert status.current_version == "1.2.0"


def test_update_runner_returns_four_when_activation_rolls_back(
    tmp_path: Path,
    monkeypatch,
) -> None:
    descriptor = tmp_path / "staged.json"
    descriptor.write_text('{"version":"1.2.0","artifacts":[]}', encoding="utf-8")

    class RolledBackCoordinator:
        def apply_staged(self, path: Path, wait_pid: int | None) -> ActivationResult:
            return ActivationResult(activated=False, rolled_back=True)

    monkeypatch.setattr(
        update_runner,
        "build_coordinator",
        lambda _: RolledBackCoordinator(),
    )

    assert update_runner.run(["--apply", str(descriptor)], data_root=tmp_path) == 4
    assert UpdateStatusStore(tmp_path / "updates" / "status.json").read().state == "error"


def test_update_lock_prevents_a_second_updater_for_the_same_data_root(
    tmp_path: Path,
) -> None:
    first = UpdateRunLock(tmp_path / "updates" / "updater.lock")
    second = UpdateRunLock(tmp_path / "updates" / "updater.lock")

    assert first.acquire() is True
    try:
        assert second.acquire() is False
    finally:
        first.release()

    assert second.acquire() is True
    second.release()
