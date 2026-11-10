"""Hermes MTG Deck Studio: agent half."""
import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent


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


def _tool(name):
    def handler(args, session_id=None, **kwargs):
        return _core().tools.dispatch(name, args or {}, session_id=session_id)

    return handler


def _pre_llm_call(session_id=None, **kwargs):
    return _core().context.pre_llm_call(session_id=session_id)


def register(ctx):
    core = _core()
    for schema in core.tools.schemas():
        ctx.register_tool(
            name=schema["name"], toolset="mtg_deck_studio", schema=schema, handler=_tool(schema["name"]), emoji="🃏"
        )
    ctx.register_hook("pre_llm_call", _pre_llm_call)
    ctx.register_skill("mtg-deck-studio", _ROOT / "skills" / "mtg-deck-studio" / "SKILL.md")
