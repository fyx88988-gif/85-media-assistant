from collections.abc import Iterator
from pathlib import Path

import pytest

from update_acceptance_fixture import InstalledDeviceFixture


@pytest.fixture
def installed_fixture(tmp_path: Path) -> Iterator[InstalledDeviceFixture]:
    fixture = InstalledDeviceFixture(tmp_path)
    try:
        yield fixture
    finally:
        fixture.close()


def test_one_install_upgrades_twice_and_rolls_back_corrupt_third_release(
    installed_fixture: InstalledDeviceFixture,
) -> None:
    app = installed_fixture.install("1.0.0")
    app.publish_valid_update("1.1.0")
    app.check_apply_and_restart()
    assert app.health()["productVersion"] == "1.1.0"

    app.publish_valid_update("1.2.0")
    app.check_apply_and_restart()
    assert app.health()["productVersion"] == "1.2.0"

    app.publish_corrupt_update("1.3.0")
    app.check_apply_and_restart()
    assert app.health()["productVersion"] == "1.2.0"
    assert app.download_fixture.read_bytes() == b"keep-user-video"
    assert app.installer_run_count == 1
