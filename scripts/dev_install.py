"""Install the package for development under a separate plugin id.

Copies plugin/ to work/dev/<id>/ with the plugin id substituted, adds the
.dev-reload marker, and links it into HERMES_HOME/plugins/<id> with a junction on
first run. Re-run after every edit: Desktop re-syncs the linked copy and the core
reloads changed Python modules on its next call.

    python scripts/dev_install.py [--id mtg-deck-studio-dev]
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
SOURCE = PROJECT / "plugin"
SUBSTITUTE = {
    "plugin.yaml": [("name: mtg-deck-studio\n", "name: {id}\n")],
    "dashboard/manifest.json": [('"name": "mtg-deck-studio"', '"name": "{id}"')],
    "desktop/plugin.js": [("const PLUGIN_ID = 'mtg-deck-studio'", "const PLUGIN_ID = '{id}'"), ("name: 'MTG Deck Studio',", "name: 'MTG Deck Studio ({id})',")],
}


def sync(plugin_id: str) -> Path:
    target = PROJECT / "work" / "dev" / plugin_id
    target.mkdir(parents=True, exist_ok=True)
    for path in SOURCE.rglob("*"):
        rel = path.relative_to(SOURCE)
        if "__pycache__" in rel.parts or rel.parts[0] == "tests":
            continue
        dest = target / rel
        if path.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
            continue
        data = path.read_bytes()
        for old, new in SUBSTITUTE.get(rel.as_posix(), []):
            text = data.decode("utf-8")
            if old in text:
                data = text.replace(old, new.format(id=plugin_id)).encode("utf-8")
                continue
            # A CRLF checkout (or a file the patch tool rewrote) still has to work.
            crlf_old = old.replace(chr(10), chr(13) + chr(10))
            crlf_new = new.replace(chr(10), chr(13) + chr(10))
            if crlf_old not in text:
                sys.exit(f"substitution anchor missing in {rel}: {old!r}")
            data = text.replace(crlf_old, crlf_new.format(id=plugin_id)).encode("utf-8")
        if not dest.exists() or dest.read_bytes() != data:
            dest.write_bytes(data)
    for path in target.rglob("*"):
        rel = path.relative_to(target)
        if path.is_file() and rel.as_posix() != ".dev-reload" and not (SOURCE / rel).exists():
            path.unlink()
    (target / ".dev-reload").write_text("development reload marker\n", encoding="utf-8")
    return target


def check(plugin_id: str) -> int:
    """Report files where the live copy differs from plugin/. Exit 1 when the plugin is stale."""
    target = PROJECT / "work" / "dev" / plugin_id
    if not target.exists():
        print(f"live copy missing: {target}")
        return 1
    stale, missing = [], []
    for path in SOURCE.rglob("*"):
        rel = path.relative_to(SOURCE)
        if "__pycache__" in rel.parts or rel.parts[0] == "tests" or not path.is_file():
            continue
        dest = target / rel
        if not dest.exists():
            missing.append(rel.as_posix())
            continue
        data = path.read_bytes()
        for old, new in SUBSTITUTE.get(rel.as_posix(), []):
            text = data.decode("utf-8")
            rendered = new.format(id=plugin_id)
            # A CRLF checkout must compare the same way install() writes it, or plugin.yaml is
            # reported stale on every run even when the live copy is current.
            if old not in text:
                old = old.replace(chr(10), chr(13) + chr(10))
                rendered = rendered.replace(chr(10), chr(13) + chr(10))
            data = text.replace(old, rendered).encode("utf-8")
        if dest.read_bytes() != data:
            stale.append(rel.as_posix())
    extra = [p.relative_to(target).as_posix() for p in target.rglob("*")
             if p.is_file() and p.relative_to(target).as_posix() != ".dev-reload"
             and not (SOURCE / p.relative_to(target)).exists()]
    if not stale and not missing:
        print(f"live copy is current: {len(list(SOURCE.rglob('**/*.py')))} python files match ({target.name})")
        return 0
    for rel in stale:
        print(f"stale: {rel}")
    for rel in missing:
        print(f"missing: {rel}")
    for rel in extra:
        print(f"leftover: {rel}")
    print(f"\n{len(stale) + len(missing)} file(s) behind; run: python scripts/dev_install.py")
    return 1


def link(plugin_id: str, target: Path) -> None:
    home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    junction = home / "plugins" / plugin_id
    if junction.exists():
        return
    junction.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        f"New-Item -ItemType Junction -Path '{junction}' -Target '{target}' | Out-Null"], check=True)
    else:
        junction.symlink_to(target, target_is_directory=True)
    print(f"linked {junction} -> {target}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="report drift between plugin/ and the live copy")
    parser.add_argument("--id", default="mtg-deck-studio-dev")
    args = parser.parse_args()
    if args.check:
        return check(args.id)
    target = sync(args.id)
    link(args.id, target)
    print(f"synced {target}")


if __name__ == "__main__":
    sys.exit(main())
