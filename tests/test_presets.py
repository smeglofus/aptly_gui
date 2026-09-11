from __future__ import annotations

import pytest

from aptly_gui.services import PRESETS, get_preset


def test_ids_are_unique() -> None:
    ids = [preset.id for preset in PRESETS]
    assert len(ids) == len(set(ids))


def test_unknown_preset_is_not_an_error() -> None:
    assert get_preset("nonsense") is None
    assert get_preset(None) is None


@pytest.mark.parametrize("preset", PRESETS, ids=lambda p: p.id)
def test_every_preset_fills_the_whole_form(preset) -> None:
    form = preset.as_form()
    required = {"name", "archive_url", "suites", "components", "architectures", "publish_prefix"}
    assert required <= form.keys()
    assert all(form[field] for field in required)


def test_ubuntu_keeps_security_on_the_main_archive() -> None:
    """Ubuntu serves every suite from one host, so one set covers the release."""
    preset = get_preset("ubuntu-noble")
    assert preset is not None
    assert preset.suites == ["noble", "noble-updates", "noble-security", "noble-backports"]
    assert preset.archive_url == "http://archive.ubuntu.com/ubuntu"


def test_debian_stable_leaves_security_out() -> None:
    """trixie-security does not exist on deb.debian.org, so it cannot be in this set."""
    preset = get_preset("debian-trixie")
    assert preset is not None
    assert "trixie-security" not in preset.suites
    assert preset.note == "security"


def test_debian_security_points_at_the_security_host() -> None:
    preset = get_preset("debian-trixie-security")
    assert preset is not None
    assert preset.archive_url == "http://security.debian.org/debian-security"
    assert preset.suites == ["trixie-security"]


def test_debian_presets_do_not_collide_on_name_or_prefix() -> None:
    stable = get_preset("debian-trixie")
    security = get_preset("debian-trixie-security")
    assert stable is not None and security is not None
    assert stable.name != security.name
    assert stable.publish_prefix != security.publish_prefix
