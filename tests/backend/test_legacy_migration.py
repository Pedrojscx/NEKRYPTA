import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import legacy_migration as migration  # noqa: E402

OLD = migration.OLD


def old_home(home):
    (home / f".config/{OLD}").mkdir(parents=True)
    (home / f".config/{OLD}/config.json").write_text(json.dumps(
        {"version": 1, "terminal": "kitty", "fontSize": 18, "dnd": True, "pinnedApps": ["vivaldi-stable"], "barLayout": {"left": []}}))
    state = home / f".local/state/{OLD}"
    (state / "originals").mkdir(parents=True)
    (state / "originals/manifest.json").write_text(json.dumps({"files": [{"path": str(home / f".config/hypr/{OLD}.lua")}]}))
    (state / "wallpapers.json").write_text("{}")
    (state / "activity.sqlite3").write_text("db")
    (state / "security-session.json").write_text(json.dumps({"version": 2, "session": True, "changedKeys": ["lockdown"]}))
    (home / f".cache/{OLD}/thumbs").mkdir(parents=True)
    shell = home / f".config/quickshell/{OLD}"
    shell.mkdir(parents=True)
    (shell / "shell.qml").write_text("x")
    (home / ".local/bin").mkdir(parents=True)
    (home / f".local/bin/{OLD}").write_text("#!/bin/sh")
    (home / ".config/systemd/user").mkdir(parents=True)
    (home / f".config/systemd/user/{OLD}.service").write_text("[Unit]")
    hypr = home / ".config/hypr"
    hypr.mkdir(parents=True)
    for name in (f"{OLD}.lua", f"{OLD}-hypridle.conf", f"{OLD}-hyprlock.conf"):
        (hypr / name).write_text("x")
    (hypr / "hyprland.lua").write_text(
        f'-- {OLD.capitalize()} replaced: hl.bind("SUPER + SPACE", x)\n-- {OLD.capitalize()} shell integration\n'
        f'do local chunk = loadfile(os.getenv("HOME") .. "/.config/hypr/{OLD}.lua") if chunk then chunk() end end\n')


class LegacyMigrationTests(unittest.TestCase):
    def run_migration(self, home, **kwargs):
        log = []
        result = migration.migrate(home, apply=True, report=log.append, environ={"XDG_RUNTIME_DIR": str(home / "run")},
                                   live=False, **kwargs)
        return result, log

    def test_nothing_happens_without_an_old_installation(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertFalse(migration.migrate(Path(directory), apply=True, live=False, environ={}))

    def test_preview_changes_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            old_home(home)
            migration.migrate(home, apply=False, report=lambda text: None, environ={}, live=False)
            self.assertTrue((home / f".config/{OLD}/config.json").exists())

    def test_state_snapshot_and_preferences_move_and_the_old_files_go(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            old_home(home)
            migrated, log = self.run_migration(home)
            self.assertTrue(migrated)
            state = home / ".local/state/nekrypta"
            snapshot = state / "security-session.json"
            self.assertEqual(stat.S_IMODE(snapshot.stat().st_mode), 0o600)
            self.assertTrue(json.loads(snapshot.read_text())["session"])
            self.assertEqual(stat.S_IMODE((state / "state.json").stat().st_mode), 0o600)
            # Only operational values are kept; the fixed look is not carried over.
            self.assertEqual(json.loads((state / "state.json").read_text()), {"version": 1, "dnd": True, "pinnedApps": ["vivaldi-stable"]})
            self.assertTrue((state / "wallpapers.json").exists())
            self.assertTrue((state / "activity.sqlite3").exists())
            self.assertIn("nekrypta.lua", (state / "originals/manifest.json").read_text())
            self.assertEqual(migration.found(home, {"XDG_RUNTIME_DIR": str(home / "run")}), [])
            self.assertFalse(any(OLD in str(path) for path in home.rglob("*") if "before-nekrypta" not in path.name))

    def test_hyprland_installer_lines_are_renamed_with_a_copy_kept(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            old_home(home)
            self.run_migration(home)
            text = (home / ".config/hypr/hyprland.lua").read_text()
            self.assertIn("-- NEKRYPTA replaced: ", text)
            self.assertIn("-- NEKRYPTA shell integration", text)
            self.assertIn(".config/hypr/nekrypta.lua", text)
            self.assertNotIn(OLD, text.lower())
            self.assertEqual(len(list((home / ".config/hypr").glob("hyprland.lua.before-nekrypta-*"))), 1)

    def test_an_invalid_snapshot_is_not_carried_over(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            old_home(home)
            (home / f".local/state/{OLD}/security-session.json").write_text("not json")
            self.run_migration(home)
            self.assertFalse((home / ".local/state/nekrypta/security-session.json").exists())

    def test_running_it_twice_is_harmless(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            old_home(home)
            self.run_migration(home)
            migrated, _ = self.run_migration(home)
            self.assertFalse(migrated)


if __name__ == "__main__":
    unittest.main()
