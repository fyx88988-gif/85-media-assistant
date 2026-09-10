from pathlib import Path

from media_assistant.local_session import LocalSessionAuthority


def test_second_authority_instance_validates_a_fresh_token(tmp_path: Path) -> None:
    key_file = tmp_path / "settings" / "local-session.key"
    first = LocalSessionAuthority.load_or_create(key_file)
    second = LocalSessionAuthority.load_or_create(key_file)

    assert second.validate(first.issue(now=1_000), now=1_001) is True
    assert key_file.read_bytes() == first.secret


def test_expired_or_modified_local_token_is_rejected(tmp_path: Path) -> None:
    authority = LocalSessionAuthority.load_or_create(tmp_path / "local-session.key")
    token = authority.issue(now=1_000)

    assert authority.validate(token, now=1_000 + authority.ttl_seconds + 1) is False
    assert authority.validate(token + "x", now=1_001) is False


def test_token_from_another_install_is_rejected(tmp_path: Path) -> None:
    first = LocalSessionAuthority.load_or_create(tmp_path / "first.key")
    second = LocalSessionAuthority.load_or_create(tmp_path / "second.key")

    assert second.validate(first.issue(now=1_000), now=1_001) is False
