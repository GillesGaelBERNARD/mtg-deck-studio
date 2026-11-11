# Notices

MTG Deck Studio is MIT licensed (see `LICENSE`); the licence covers every file in this
repository. Third-party data sources, trademarks and the provenance of bundled helpers are noted
below.

## Card data and images — Scryfall

Oracle text, mana costs, printings and card images are fetched at runtime from the public
[Scryfall API](https://scryfall.com/docs/api) and are not redistributed here. Scryfall's own
terms govern that data.

## Bundled helpers

Two scripts are vendored verbatim under `plugin/vendor/` (MIT, same licence as the rest of this
repository) and are run as subprocesses with the host's Python interpreter:

| File | Upstream | Licence | Used by |
| --- | --- | --- | --- |
| `deck_evidence.py` | `mtg-deck-strategy-notes` (scripts/) | MIT | `plugin/core/strategy.py` |
| `mtg_viewer_to_moxfield.py` | `mtg-viewer-to-moxfield` (scripts/) | MIT | `plugin/core/exports.py` |

They read and write only the paths the plugin hands them (deck JSON under the plugin data
directory, a temporary file for the export), open no sockets, and are not modified in place —
`plugin/vendor/README.md` says how to refresh each from its source.

## Hermes Agent — the host

The plugin runs inside [Hermes Agent](https://github.com/NousResearch/hermes-agent)
(MIT, Nous Research). No Hermes source is vendored here; the plugin uses only its public
surfaces (`register_tool`, `register_hook`, the Desktop plugin SDK, the dashboard plugin
API).

## Magic: The Gathering

Magic: The Gathering is a trademark of Wizards of the Coast. This project is unofficial and
is not endorsed or sponsored by Wizards of the Coast.

## Your decks

Deck files, sideboards, scans and saved JSON are your own data. They live in the plugin's
profile-scoped data directory, are never bundled with the plugin and are never uploaded
anywhere; the only outbound requests are to Scryfall.
