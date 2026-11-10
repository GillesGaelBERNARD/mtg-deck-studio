"""Where Hermes keeps this profile's data, without hardcoding anybody's home directory.

The maintenance scripts run outside Hermes, so they resolve the home the same way the host
does: HERMES_HOME when it is set, then the hermes folder inside the Windows local app-data
directory, then ~/.hermes. plugin_data() adds the plugin-data folder for a plugin id.
"""
from __future__ import annotations

import os
from pathlib import Path


def hermes_home() -> Path:
    env = os.environ.get("HERMES_HOME")
    if env:
        return Path(env)
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata and (Path(local_appdata) / "hermes").is_dir():
        return Path(local_appdata) / "hermes"
    return Path.home() / ".hermes"


def plugin_data(plugin_id: str = "mtg-deck-studio-dev") -> Path:
    return hermes_home() / "plugin-data" / plugin_id
