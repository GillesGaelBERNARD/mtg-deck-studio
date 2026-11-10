"""Core of the MTG Deck Studio, shared by the agent half and the backend routes.

Both halves load this package once under sys.modules["mtg_deck_studio_core"], so they
share one bridge. A `.dev-reload` file beside plugin.yaml reloads changed modules on
the next call; shared state lives outside the modules and survives the reload.
"""
import importlib
import sys
from pathlib import Path

_DIR = Path(__file__).resolve().parent


def _plugin_id() -> str:
    for line in (_DIR.parent / "plugin.yaml").read_text(encoding="utf-8").splitlines():
        if line.startswith("name:"):
            return line.split(":", 1)[1].strip().strip('"')
    raise RuntimeError("plugin.yaml has no name")


PLUGIN_ID = _plugin_id()
_MODULES = ("errors", "operations", "store", "bridge", "summary", "viewer", "strategy", "scryfall", "exports", "context", "links", "rpc", "tools")
_loaded_mtime = 0.0


def _mtime():
    return max(p.stat().st_mtime for p in [*_DIR.glob("*.py"), _DIR.parent / "operations.json"])


def _load():
    global _loaded_mtime
    pkg = sys.modules[__name__]
    for name in _MODULES:
        full = f"{__name__}.{name}"
        mod = sys.modules.get(full)
        mod = importlib.reload(mod) if mod else importlib.import_module(full)
        setattr(pkg, name, mod)
    _loaded_mtime = _mtime()


def current():
    if not _loaded_mtime or ((_DIR.parent / ".dev-reload").exists() and _mtime() > _loaded_mtime):
        _load()
    return sys.modules[__name__]
