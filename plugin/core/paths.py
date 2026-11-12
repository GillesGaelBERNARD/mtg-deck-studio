"""Turn a caller-supplied path into a local one, or refuse it.

The agent can name an import path (``mtg_deck_studio_decks action=import``), so a path is
untrusted input. A bare ``is_absolute()`` is not enough on Windows: ``\\\\host\\share\\x.json``,
``//host/share/x.json``, ``\\\\?\\C:\\x`` and ``\\\\.\\X`` all report absolute, and the first two
make the machine open an SMB session — and hand over the user's NTLM credentials — to a server
the caller chose. The same reasoning applies to a device path, which reaches hardware instead of
a file.

Every filesystem call in the plugin goes through this first, so nothing is stat()-ed before the
path has been cleared, and a local junction pointing at a share is refused too.
"""
from __future__ import annotations

from pathlib import Path, PureWindowsPath

from .errors import StudioError

__all__ = ["safe_local_path"]

_UNC_PREFIXES = ("\\\\", "//")
_DEVICE_PREFIXES = ("\\\\?\\", "\\\\.\\")


def _refuse(raw: str, why: str) -> "StudioError":
    return StudioError(
        "invalid",
        f"Refusing {why}: {raw}. Give a path on a local drive inside a folder you granted.",
    )


def _network_drive(raw: str) -> bool:
    """``\\\\host\\share``, ``\\\\?\\C:`` and ``\\\\.\\X`` all land in ``.drive``."""
    drive = PureWindowsPath(raw).drive
    return drive.startswith("\\\\")


def safe_local_path(raw: object) -> Path:
    """Return an absolute local :class:`Path`, or raise ``StudioError('invalid')``.

    Rejects, in order: an empty value, a UNC path (either separator), a device path, a path
    that is not absolute on this host. Only then does it touch the filesystem, by resolving —
    and it re-checks the resolved form, because a local junction can point at a share.
    """
    text = str(raw or "").strip().strip('"').strip("'")
    if not text:
        raise StudioError("invalid", "Give an absolute local path.")

    if text.startswith(_UNC_PREFIXES) or text.startswith(_DEVICE_PREFIXES) or _network_drive(text):
        raise _refuse(text, "a network or device path")

    path = Path(text)
    if not path.is_absolute():
        raise StudioError("invalid", f"The path must be absolute: {text}.")

    resolved = path.resolve()
    if _network_drive(str(resolved)):
        # A junction or mount point inside the granted folder aimed at a share.
        raise _refuse(text, "a path that resolves to a network location")
    return resolved
