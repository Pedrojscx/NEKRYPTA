import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "helpers"))
sys.path.insert(0, str(ROOT / "scripts"))

from helpers import backend, personal  # noqa: E402

# The only places the previous product name may still appear.
ALLOWED = {"LICENSE", "docs/ATRIBUICAO.md", "scripts/legacy_migration.py",
           "tests/backend/test_naming_and_config.py", "tests/backend/test_legacy_migration.py"}
SKIP_DIRS = {".git", "graphify-out", "__pycache__", ".pytest_cache"}
OLD = "sha" + "le"


def repository_files():
    for directory, names, files in os.walk(ROOT):
        names[:] = [name for name in names if name not in SKIP_DIRS]
        for file in files:
            path = Path(directory) / file
            if path.suffix in {".png", ".gif", ".sqlite3", ".pyc"}:
                continue
            yield path


class NamingTests(unittest.TestCase):
    def test_previous_name_only_survives_in_the_legal_and_migration_files(self):
        found = []
        for path in repository_files():
            relative = path.relative_to(ROOT).as_posix()
            if OLD in relative.lower():
                found.append(relative)
            elif relative not in ALLOWED:
                try:
                    if OLD in path.read_text().lower():
                        found.append(relative)
                except UnicodeDecodeError:
                    pass
        self.assertEqual(found, [])

    def test_new_contracts(self):
        service = (ROOT / "integration/nekrypta.service").read_text()
        self.assertIn("ExecStart=%h/.local/bin/nekrypta run", service)
        self.assertEqual(backend.STATE.name, "nekrypta")
        self.assertEqual(backend.CACHE.name, "nekrypta")
        self.assertEqual(backend.RUNTIME.name, "nekrypta")
        self.assertTrue(backend.NEKRYPTA_COMMAND.endswith("/.local/bin/nekrypta"))
        lua = (ROOT / "integration/nekrypta.lua").read_text()
        self.assertIn('namespace = "^nekrypta.*"', lua)
        self.assertNotIn("record", lua)
        for name in ("scripts/nekrypta", "integration/nekrypta.lua", "integration/nekrypta-wallpaper.lua",
                     "integration/nekrypta.service"):
            self.assertTrue((ROOT / name).exists(), name)
        self.assertIn("NEKRYPTA_*", "NEKRYPTA_*")
        self.assertIn("nekrypta_eq", (ROOT / "helpers/equalizer.py").read_text())
        self.assertIn("/org/nekrypta/BluetoothAgent", (ROOT / "helpers/backend.py").read_text())
        self.assertIn("NEKRYPTA/1.0", (ROOT / "helpers/weather.py").read_text())

    def test_every_layer_surface_uses_the_new_namespace(self):
        namespaces = set()
        for path in (ROOT / "features").glob("*.qml"):
            namespaces.update(re.findall(r'WlrLayershell\.namespace: "([^"]+)"', path.read_text()))
        self.assertTrue(namespaces)
        self.assertTrue(all(name.startswith("nekrypta-") for name in namespaces), namespaces)

    def test_update_mechanism_is_gone(self):
        self.assertFalse((ROOT / "helpers/update.py").exists())
        self.assertFalse((ROOT / "services/Updates.qml").exists())


class ShortcutTests(unittest.TestCase):
    def table(self):
        text = (ROOT / "integration/nekrypta.lua").read_text()
        return dict(re.findall(r'\["([^"]+)"\] = "([^"]+)"', re.search(r"local shortcuts = \{(.*?)\n\}", text, re.S)[1]))

    def test_super_space_and_super_r_open_the_launcher(self):
        table = self.table()
        self.assertEqual(table["SUPER + SPACE"], "launcher")
        self.assertEqual(table["SUPER + R"], "launcher")
        bound = {item["keys"]: item["action"] for item in personal.SHORTCUTS}
        self.assertEqual(bound["SUPER + SPACE"], bound["SUPER + R"])

    def test_the_lua_table_is_the_fixed_configuration(self):
        fixed = {item["keys"]: item["action"] for item in personal.SHORTCUTS if not item.get("optIn")}
        self.assertEqual(self.table(), fixed)

    def test_installer_renders_both_launcher_shortcuts(self):
        import install
        rendered = install.nekrypta_lua(install.SHORTCUTS)
        self.assertIn('["SUPER + SPACE"] = "launcher"', rendered)
        self.assertIn('["SUPER + R"] = "launcher"', rendered)
        self.assertNotIn("terminal", rendered.lower())

    def test_super_r_clash_is_detected(self):
        import install
        # The user's own runner on Super+R, the way their config writes it, is found and offered for replacement.
        config = 'local mainMod = "SUPER"\nhl.bind(mainMod .. " + " .. "R", hl.dsp.exec_cmd("wofi --show drun"))\n'
        found = install.conflicts(config, install.SHORTCUTS)
        self.assertEqual(list(found), [install.normalize("SUPER + R")])
        texts, chosen, skipped = install.integrate({Path("hyprland.lua"): config}, install.SHORTCUTS, lambda *_: True)
        self.assertIn(("SUPER + R", "launcher"), [(keys, action) for keys, action, _ in chosen])
        self.assertIn("-- NEKRYPTA replaced: hl.bind(mainMod", next(iter(texts.values())))

    def test_runtime_customization_is_gone(self):
        self.assertFalse((ROOT / "helpers/shortcuts.py").exists())
        self.assertFalse((ROOT / "helpers/shortcuts.json").exists())


class PersonalConfigTests(unittest.TestCase):
    def test_fixed_values_come_from_the_single_source(self):
        self.assertEqual(personal.FIXED["terminal"], "kitty")
        self.assertEqual(personal.FIXED["language"], "pt_BR")
        self.assertIs(personal.FIXED["allowTailscaleMinecraft"], True)
        self.assertEqual(personal.FIXED["weatherLocation"]["name"], "Jacareí")
        self.assertEqual(backend.DEFAULTS["accentColor"], personal.PERSONAL["palette"]["accent"])

    def test_the_old_config_file_is_never_read(self):
        with tempfile.TemporaryDirectory() as directory:
            old = Path(directory) / "config.json"
            old.write_text(json.dumps({"version": 1, "terminal": "foot", "fontSize": 18, "dnd": True}))
            with patch.object(backend, "CONFIG", Path(directory) / "state.json"):
                current = backend.settings()
            self.assertEqual(current["terminal"], "kitty")
            self.assertEqual(current["fontSize"], 14)
            self.assertFalse(current["dnd"])

    def test_only_operational_state_is_saved_and_fixed_keys_are_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "state.json"
            with patch.object(backend, "CONFIG", target):
                backend.save_settings({"dnd": True, "fontSize": 18, "terminal": "foot", "barLayout": {}})
                saved = json.loads(target.read_text())
                self.assertEqual(saved, {"version": 1, "dnd": True})
                self.assertEqual(backend.settings()["fontSize"], 14)

    def test_every_qml_setting_has_a_home(self):
        keys = set()
        for path in list((ROOT / "features").glob("*.qml")) + list((ROOT / "services").glob("*.qml")) + list((ROOT / "core").glob("*.qml")):
            keys.update(re.findall(r'[sS]etting\("([A-Za-z0-9]+)"', path.read_text()))
        known = set(personal.FIXED) | set(personal.OPERATIONAL_KEYS)
        self.assertEqual(sorted(keys - known), [])


if __name__ == "__main__":
    unittest.main()
