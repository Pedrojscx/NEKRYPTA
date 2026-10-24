#!/usr/bin/env python3
"""Temporary: moves an installation made under the project's previous name to NEKRYPTA.

This is the only code that still knows the old name. It stops the old service, carries over the state that
matters (the security snapshot, activity history, wallpaper choices, operational preferences), rewrites the lines
the old installer put in hyprland.lua, removes the old files, units, directories and command, and leaves the new
installer to put NEKRYPTA in place. Nothing here runs unless an old installation is found."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'helpers'))
import personal  # noqa: E402

OLD = 'shale'
NEW = 'nekrypta'
# Old line -> new line in hyprland.lua (the installer's markers, its "replaced" comments and its loader lines).
LINE_RULES = (
    (re.compile(r'^-- Shale shell integration$', re.M), '-- NEKRYPTA shell integration'),
    (re.compile(r'^-- Shale wallpaper shortcut$', re.M), '-- NEKRYPTA wallpaper shortcut'),
    (re.compile(r'^-- Shale replaced: ', re.M), '-- NEKRYPTA replaced: '),
    (re.compile(r'(\.config/hypr/)shale(-wallpaper)?\.lua'), r'\1nekrypta\2.lua'),
)
# Restricted permissions for the state that can describe the network.
PRIVATE = 0o600


def paths(home, environ=os.environ):
    xdg_runtime = environ.get('XDG_RUNTIME_DIR')
    return {
        'config': home / '.config' / OLD, 'state': home / '.local/state' / OLD, 'cache': home / '.cache' / OLD,
        'shell': home / '.config/quickshell' / OLD, 'command': home / '.local/bin' / OLD,
        'unit': home / '.config/systemd/user' / f'{OLD}.service',
        'hypr': home / '.config/hypr', 'runtime': Path(xdg_runtime) / OLD if xdg_runtime else None,
        'tmp': Path(f'/tmp/{OLD}-{os.getuid()}'),
    }


def hypr_files(hypr):
    return [hypr / f'{OLD}.lua', hypr / f'{OLD}-wallpaper.lua', hypr / f'{OLD}-hypridle.conf', hypr / f'{OLD}-hyprlock.conf']


def found(home, environ=os.environ):
    """What of the old installation exists on this machine."""
    where = paths(home, environ)
    candidates = [where[key] for key in ('config', 'state', 'cache', 'shell', 'command', 'unit', 'runtime', 'tmp') if where[key]]
    return [path for path in [*candidates, *hypr_files(where['hypr'])] if path.exists() or path.is_symlink()]


def write_private(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name('.' + path.name + '.migrating')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, PRIVATE)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def migrate_security_snapshot(old_state, new_state, report):
    """The security snapshot goes over only when it is a readable session; it keeps restricted permissions."""
    source = old_state / 'security-session.json'
    if not source.is_file():
        return
    try:
        saved = json.loads(source.read_text())
        valid = isinstance(saved, dict) and saved.get('session') is True and saved.get('version') in (1, 2)
    except (OSError, ValueError):
        valid = False
    if not valid:
        report('security snapshot: not a valid session, left behind')
        return
    target = new_state / 'security-session.json'
    if target.exists():
        report('security snapshot: kept the one already in the new state')
        return
    write_private(target, source.read_bytes())
    report('security snapshot: migrated (permissions 0600)')


def migrate_preferences(old_config, new_state, report):
    """Only the operational keys of the old config.json go over; the look is fixed in core/personal.json."""
    source = old_config / 'config.json'
    target = new_state / 'state.json'
    if not source.is_file() or target.exists():
        return
    try:
        saved = json.loads(source.read_text())
    except (OSError, ValueError):
        report('preferences: unreadable, left behind')
        return
    kept = {key: saved[key] for key in personal.OPERATIONAL_KEYS if isinstance(saved, dict) and key in saved
            and saved[key] != personal.INITIAL.get(key)}
    write_private(target, json.dumps({'version': 1, **kept}, ensure_ascii=False).encode())
    report(f'preferences: {len(kept)} operational value(s) migrated, the rest is fixed in core/personal.json')


def migrate_state(old_state, new_state, report):
    new_state.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name in ('wallpapers.json', 'system.json'):
        source = old_state / name
        if source.is_file() and not (new_state / name).exists():
            shutil.copy2(source, new_state / name)
    for source in old_state.glob('activity.sqlite3*'):
        if not (new_state / source.name).exists():
            shutil.copy2(source, new_state / source.name)
    # What each file looked like before the installer first wrote it, with its paths under the new names.
    originals = old_state / 'originals'
    if originals.is_dir() and not (new_state / 'originals').exists():
        shutil.copytree(originals, new_state / 'originals')
        manifest = new_state / 'originals' / 'manifest.json'
        if manifest.is_file():
            text = manifest.read_text()
            text = text.replace(f'.config/quickshell/{OLD}', f'.config/quickshell/{NEW}').replace(f'/hypr/{OLD}', f'/hypr/{NEW}')
            text = text.replace(f'.local/bin/{OLD}', f'.local/bin/{NEW}').replace(f'user/{OLD}.service', f'user/{NEW}.service')
            manifest.write_text(text)
    migrate_security_snapshot(old_state, new_state, report)


def rewrite_hyprland(hypr, report, now=time.strftime):
    """hyprland.lua's installer lines under the new names, after a copy of the file as it was."""
    config = hypr / 'hyprland.lua'
    if not config.is_file():
        return
    text = config.read_text()
    changed = text
    for pattern, replacement in LINE_RULES:
        changed = pattern.sub(replacement, changed)
    if changed != text:
        shutil.copy2(config, hypr / f'hyprland.lua.before-nekrypta-{now("%Y%m%d-%H%M%S")}')
        config.write_text(changed)
        report('hyprland.lua: installer lines renamed (a copy of the previous file was kept next to it)')


def remove(path, report):
    try:
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
        else:
            return
        report(f'removed {path}')
    except OSError as error:
        report(f'could not remove {path}: {error.strerror}')


def systemctl(*args, run=subprocess.run):
    try:
        return run(['systemctl', '--user', *args], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None


def old_processes(run=subprocess.run):
    """PIDs of this user's processes still running from the old installation."""
    try:
        listing = run(['pgrep', '-u', str(os.getuid()), '-af', f'quickshell --path .*/{OLD}|/{OLD}/helpers/backend.py'],
                      capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [int(line.split()[0]) for line in listing.splitlines() if line.strip() and 'pgrep' not in line]


def migrate(home, apply=False, report=print, environ=os.environ, run=subprocess.run, live=True):
    """Moves an old installation to the new names. Returns whether there was one. `live` also touches systemd."""
    existing = found(home, environ)
    if not existing:
        return False
    if not apply:
        report('Old installation found; with --apply it is migrated: ' + ', '.join(str(path) for path in existing))
        return True
    where = paths(home, environ)
    report('Migrating the old installation')
    if live:
        systemctl('stop', f'{OLD}.service', run=run)
        systemctl('disable', f'{OLD}.service', run=run)
    if where['state'].is_dir():
        migrate_state(where['state'], home / '.local/state' / NEW, report)
    migrate_preferences(where['config'], home / '.local/state' / NEW, report)
    rewrite_hyprland(where['hypr'], report)
    for path in [*hypr_files(where['hypr']), where['command'], where['unit'], where['shell'], where['config'],
                 where['state'], where['cache'], where['runtime'], where['tmp']]:
        if path is not None:
            remove(path, report)
    wants = home / '.config/systemd/user/graphical-session.target.wants' / f'{OLD}.service'
    remove(wants, report)
    if live:
        systemctl('daemon-reload', run=run)
        systemctl('reset-failed', f'{OLD}.service', run=run)
        for pid in old_processes(run):
            try:
                os.kill(pid, 15)
            except OSError:
                pass
        remaining = old_processes(run)
        if remaining:
            report(f'WARNING: {len(remaining)} old process(es) still running')
    return True


if __name__ == '__main__':
    migrate(Path.home(), apply='--apply' in sys.argv)
