"""This rice's fixed configuration: core/personal.json read as the flat settings the rest of the helper uses.

The file is the only place the visual and functional choices live. Nothing here is saved or changed at run time;
what the user does while the shell runs (do not disturb, history, timers) is operational state, kept apart."""
import json
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / "core" / "personal.json"
PERSONAL = json.loads(PATH.read_text())

def _dig(path):
    value = PERSONAL
    for part in path.split("."):
        value = value[part]
    return value


# flat setting name -> value at its path in personal.json ("bindings")
FIXED = {key: _dig(path) for key, path in PERSONAL["bindings"].items()}
INITIAL = dict(PERSONAL["operationalDefaults"])
SECURITY = PERSONAL["security"]
MULLVAD = SECURITY["mullvad"]
SHORTCUTS = PERSONAL["shortcuts"]
# Operational state: what the user does while the shell runs. Kept apart from the fixed configuration above.
OPERATIONAL_KEYS = ("pinnedApps", "appUsage", "emojiUsage", "bluetoothAutoConnect", "eqBands", "focusSession", "timers", *INITIAL)
