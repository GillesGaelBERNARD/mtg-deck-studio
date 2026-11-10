"""Hermes MTG Deck Studio: backend routes. All logic lives in core."""
import importlib.util
import sys
from pathlib import Path

from fastapi import APIRouter, Body

_ROOT = Path(__file__).resolve().parent.parent
router = APIRouter()


_KEY = "mtg_deck_studio_core__" + _ROOT.name.replace("-", "_")


def _core():
    mod = sys.modules.get(_KEY)
    if mod is None:
        spec = importlib.util.spec_from_file_location(
            _KEY, _ROOT / "core" / "__init__.py", submodule_search_locations=[str(_ROOT / "core")]
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules[_KEY] = mod
        spec.loader.exec_module(mod)
    return mod.current()


@router.get("/viewer")
def viewer():
    return _core().viewer.payload()


@router.post("/rpc")
def rpc(body: dict = Body(...)):
    return _core().rpc.call(body.get("method"), body.get("params") or {})
