from __future__ import annotations

from dataclasses import dataclass

UBUNTU_KEYRING = "/usr/share/keyrings/ubuntu-archive-keyring.gpg"
DEBIAN_KEYRING = "/usr/share/keyrings/debian-archive-keyring.gpg"

UBUNTU_COMPONENTS = ["main", "restricted", "universe", "multiverse"]
DEBIAN_COMPONENTS = ["main", "contrib", "non-free", "non-free-firmware"]


@dataclass(frozen=True)
class Preset:
    """A starting point for a new mirror set, not a constraint — every field stays editable."""

    id: str
    label: str
    note: str
    name: str
    archive_url: str
    suites: list[str]
    components: list[str]
    keyrings: list[str]
    publish_prefix: str
    architectures: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.architectures is None:
            object.__setattr__(self, "architectures", ["amd64"])

    def as_form(self) -> dict[str, str]:
        return {
            "name": self.name,
            "archive_url": self.archive_url,
            "suites": ", ".join(self.suites),
            "components": ", ".join(self.components),
            "architectures": ", ".join(self.architectures),
            "keyrings": ", ".join(self.keyrings),
            "publish_prefix": self.publish_prefix,
            "signing_key": "",
            "filter": "",
        }


def _ubuntu(code: str, version: str) -> Preset:
    return Preset(
        id=f"ubuntu-{code}",
        label=f"Ubuntu {version} ({code})",
        note="",
        name=f"ubuntu-{code}",
        archive_url="http://archive.ubuntu.com/ubuntu",
        suites=[code, f"{code}-updates", f"{code}-security", f"{code}-backports"],
        components=UBUNTU_COMPONENTS,
        keyrings=[UBUNTU_KEYRING],
        publish_prefix="ubuntu",
    )


def _debian(code: str, version: str) -> Preset:
    return Preset(
        id=f"debian-{code}",
        label=f"Debian {version} ({code})",
        # Security lives on a different host, so it cannot share this set's archive URL.
        note="security",
        name=f"debian-{code}",
        archive_url="http://deb.debian.org/debian",
        suites=[code, f"{code}-updates", f"{code}-backports"],
        components=DEBIAN_COMPONENTS,
        keyrings=[DEBIAN_KEYRING],
        publish_prefix="debian",
    )


def _debian_security(code: str, version: str) -> Preset:
    return Preset(
        id=f"debian-{code}-security",
        label=f"Debian {version} security ({code}-security)",
        note="",
        name=f"debian-{code}-security",
        archive_url="http://security.debian.org/debian-security",
        suites=[f"{code}-security"],
        components=DEBIAN_COMPONENTS,
        keyrings=[DEBIAN_KEYRING],
        publish_prefix="debian-security",
    )


PRESETS: list[Preset] = [
    _ubuntu("noble", "24.04 LTS"),
    _ubuntu("jammy", "22.04 LTS"),
    _ubuntu("focal", "20.04 LTS"),
    _debian("trixie", "13"),
    _debian_security("trixie", "13"),
    _debian("bookworm", "12"),
    _debian_security("bookworm", "12"),
]

BY_ID = {preset.id: preset for preset in PRESETS}


def get_preset(preset_id: str | None) -> Preset | None:
    return BY_ID.get(preset_id) if preset_id else None
