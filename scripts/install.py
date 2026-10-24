#!/usr/bin/env python3
"""Preview, install, and conditionally restore NEKRYPTA-owned files."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import system  # noqa: E402  (hardware detection and the package plan)
import legacy_migration  # noqa: E402  (temporary: moves an installation made under the previous name)

ROOT = Path(__file__).resolve().parents[1]
BANNER = """NEKRYPTA: a Quickshell desktop shell for Hyprland

This installs the shell only: bar, launcher, control center, notifications, lock screen and
the panels around them. It is not a full set of dotfiles: your Hyprland settings, terminal,
editor, GTK/Qt themes and wallpapers stay yours. It only adds NEKRYPTA's own shortcuts, asking
before it replaces one of yours, and backs up every file it changes.
"""
MARKER = '-- NEKRYPTA shell integration'
WALLPAPER_MARKER = '-- NEKRYPTA wallpaper shortcut'
REPLACED = '-- NEKRYPTA replaced: '
# How hyprland.lua loads NEKRYPTA's files: once a file is gone (after a rollback, say) the line does nothing instead of
# failing on every Hyprland start, and errors while it runs are still reported.
LOADER = 'do local chunk = loadfile(os.getenv("HOME") .. "/.config/hypr/{}") if chunk then chunk() end end'
OLD_LOADER = 'dofile(os.getenv("HOME") .. "/.config/hypr/{}")'  # what earlier installs wrote
BLOCKS = ((MARKER, 'nekrypta.lua'), (WALLPAPER_MARKER, 'nekrypta-wallpaper.lua'))
OWN = ('nekrypta.lua', 'nekrypta-wallpaper.lua')
# What runs as root is named by absolute path: pkexec starts it with a clean environment.
PACMAN, SYSTEMCTL = '/usr/bin/pacman', '/usr/bin/systemctl'
# How to become root, in the order install.sh tries them too: sudo, what people install instead of it, then pkexec
# (it asks in the terminal when there is no desktop yet).
ROOT_TOOLS = ('/usr/bin/sudo', '/usr/bin/sudo-rs', '/usr/bin/doas', '/usr/bin/pkexec')
# (keys as written in nekrypta.lua, `nekrypta` subcommand, what it opens), from the shortcuts fixed in core/personal.json.
CATALOG = json.loads((ROOT / 'core/personal.json').read_text())['shortcuts']
SHORTCUTS = [(item['keys'], item['action'], 'NEKRYPTA ' + item['title'][0].lower() + item['title'][1:])
             for item in CATALOG if item['keys'] and not item.get('optIn')]
WALLPAPER = next((item['keys'], item['action'], 'NEKRYPTA ' + item['title'].lower()) for item in CATALOG if item.get('optIn'))
# Arch packages. Required ones stop an --apply unless --force; optional ones only enable features.
REQUIRED = {'quickshell': 'quickshell', 'hyprctl': 'hyprland', 'wl-copy': 'wl-clipboard', 'python:dbus_next': 'python-dbus-next'}
OPTIONAL = {'hypridle': 'hypridle', 'hyprlock': 'hyprlock', 'cliphist': 'cliphist', 'grim': 'grim', 'slurp': 'slurp',
            'wf-recorder': 'wf-recorder', 'hyprpicker': 'hyprpicker', 'fd': 'fd'}
MINIMUM_QUICKSHELL = (0, 3, 1)
MINIMUM_HYPRLAND = (0, 56)
OTHER_SHELLS = ('waybar', 'mako', 'dunst', 'swaync', 'fnott', 'hyprpanel', 'ags')
# (folder in home, what it holds): kept by --uninstall, deleted by --uninstall --purge.
DATA = (('.local/state/nekrypta', 'security snapshot, backups and originals'),
        ('.cache/nekrypta', 'thumbnails and weather'))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def version(folder=None, root=ROOT, run=subprocess.run):
    """Which NEKRYPTA this is: the commit an installed shell folder was installed from (its COMMIT file), or the commit
    this checkout is at, with "-modified" when tracked files have uncommitted changes. 'unknown' without either."""
    if folder:
        try:
            return (Path(folder) / 'COMMIT').read_text().split()[0]
        except (OSError, IndexError):
            return 'unknown'
    try:
        head = run(['git', '-C', str(root), 'rev-parse', '--short=7', 'HEAD'], capture_output=True, text=True, timeout=10)
        if head.returncode != 0 or not head.stdout.strip():
            return 'unknown'
        changes = run(['git', '-C', str(root), 'status', '--porcelain', '--untracked-files=no'], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return 'unknown'
    return head.stdout.strip() + ('-modified' if changes.stdout.strip() else '')


# ---- Reading hyprland.lua ---------------------------------------------------------------------

def _skip_string(text, i):
    quote, i = text[i], i + 1
    while i < len(text) and text[i] not in (quote, '\n'):
        i += 2 if text[i] == '\\' else 1
    return i + 1


def _long_bracket(text, i):
    """End index of a [[...]] / [==[...]==] long string or comment body starting at i, or None."""
    match = re.match(r'\[(=*)\[', text[i:i + 64])
    if not match:
        return None
    close = text.find(']' + match[1] + ']', i + match.end())
    return len(text) if close < 0 else close + len(match[1]) + 2


def call_end(text, opening):
    """Index just past the parenthesis that closes the call opened at `opening`, skipping strings and comments."""
    depth, i = 0, opening
    while i < len(text):
        c = text[i]
        if c in '"\'':
            i = _skip_string(text, i)
            continue
        if c == '[' and (end := _long_bracket(text, i)):
            i = end
            continue
        if text.startswith('--', i):
            end = _long_bracket(text, i + 2)
            newline = text.find('\n', i)
            i = end if end else (newline if newline >= 0 else len(text))
            continue
        if c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return len(text)


def live(text):
    """`text` with comments and the insides of strings blanked, so a search finds only code. Positions are kept."""
    chars, i = list(text), 0
    while i < len(text):
        start = i
        if text[i] in '"\'':
            i = _skip_string(text, i)
            start, stop = start + 1, i - 1
        elif text[i] == '[' and (end := _long_bracket(text, i)):
            i = stop = end
        elif text.startswith('--', i):
            newline = text.find('\n', i)
            i = stop = _long_bracket(text, i + 2) or (newline if newline >= 0 else len(text))
        else:
            i += 1
            continue
        for k in range(start, min(stop, len(text))):
            if chars[k] != '\n':
                chars[k] = ' '
    return ''.join(chars)


def variables(text):
    """`local NAME = "value"` string assignments, e.g. mainMod or mod = "SUPER"."""
    found = {}
    for match in re.finditer(r'^[ \t]*(?:local[ \t]+)?([A-Za-z_]\w*)[ \t]*=[ \t]*(["\'])([^"\'\n]*)\2', text, re.M):
        found.setdefault(match[1], match[3])
    return found


def normalize(keys):
    """'shift + super + a' and 'SUPER+SHIFT+A' compare equal: sorted modifiers, then the key."""
    parts = [part.strip().upper() for part in keys.split('+') if part.strip()]
    if not parts:
        return ''
    aliases = {'MOD4': 'SUPER', 'WIN': 'SUPER', 'CTRL': 'CONTROL', ',': 'COMMA'}
    parts = [aliases.get(part, part) for part in parts]
    return '+'.join(sorted(set(parts[:-1])) + [parts[-1]])


KEY_TOKEN = r'(?:"[^"\n]*"|\'[^\'\n]*\'|[A-Za-z_]\w*)'
BIND = re.compile(r'^[ \t]*(?:local[ \t]+[A-Za-z_]\w*[ \t]*=[ \t]*)?hl\.bind[ \t]*(\()[ \t]*(' + KEY_TOKEN
                  + r'(?:[ \t]*\.\.[ \t]*' + KEY_TOKEN + r')*)[ \t]*,', re.M)


def binds(text):
    """Every live hl.bind(...) call: (start, end, normalized keys or None if unknown, action text)."""
    names = variables(text)
    found = []
    for match in BIND.finditer(text):
        keys = ''
        for token in re.split(r'[ \t]*\.\.[ \t]*', match[2]):
            if token[0] in '"\'':
                keys += token[1:-1]
            elif token in names:
                keys += names[token]
            else:
                keys = None  # a loop variable or computed key: can't know which shortcut it is
                break
        end = call_end(text, match.start(1))
        action = ' '.join(text[match.end():end - 1].split())
        found.append((match.start(), end, normalize(keys) if keys is not None else None, action[:70]))
    return found


def conflicts(text, wanted):
    """Live binds in `text` on any of the `wanted` shortcuts: {normalized keys: [(start, end, action)]}."""
    keys = {normalize(spec[0]) for spec in wanted}
    found = {}
    for start, end, key, action in binds(text):
        if key in keys:
            found.setdefault(key, []).append((start, end, action))
    return found


def comment_out(text, spans):
    """Comment every line of each call, so a multi-line bind doesn't leave a dangling body."""
    for start, end in sorted(spans, reverse=True):
        newline = text.find('\n', end)
        rest = text[end:newline if newline >= 0 else len(text)].strip()
        if rest and not rest.startswith('--'):
            text = text[:end] + '\n' + text[end:]  # code after the call stays live on its own line
        line_start = text.rfind('\n', 0, start) + 1
        line_end = text.find('\n', end - 1)
        line_end = len(text) if line_end < 0 else line_end
        block = text[line_start:line_end].split('\n')
        text = text[:line_start] + '\n'.join(REPLACED + line for line in block) + text[line_end:]
    return text


def dependents(text, spans):
    """A commented-out `local x = hl.bind(...)` leaves x nil. Its later `x:method(...)` and `x.field = value`
    statements are commented out with it: (their spans, other lines that still use x)."""
    code, more, kept = live(text), [], []
    for start, end in spans:
        name = re.match(r'[ \t]*local[ \t]+([A-Za-z_]\w*)[ \t]*=', text[start:end])
        if not name:
            continue
        statement = re.compile(r'[ \t]*' + name[1] + r'[ \t]*(?:[.:][ \t]*\w+[ \t]*(\()|\.[ \t]*\w+[ \t]*=(?!=))')
        for use in re.compile(r'(?<![\w.:])' + name[1] + r'\b').finditer(code, end):
            at = use.start()
            if any(s <= at < e for s, e in spans + more):
                continue
            line = code.rfind('\n', 0, at) + 1
            newline = code.find('\n', at)
            newline = len(code) if newline < 0 else newline
            form = None if code[line:at].strip() else statement.match(code, line)
            stop = (call_end(text, form.start(1)) if form[1] else newline) if form else newline
            if form and not code[stop:newline].strip():
                more.append((at, stop))
            elif text[line:newline].strip() not in kept:
                kept.append(text[line:newline].strip())
    return more, kept


def lua_ok(text):
    """luac's verdict on the syntax, or None when luac isn't installed."""
    if not shutil.which('luac'):
        return None
    return subprocess.run(['luac', '-p', '-'], input=text.encode(), capture_output=True).returncode == 0


LOAD = re.compile(r'''(dofile|loadfile|require)[ \t]*(\()?[ \t]*(os\.getenv[ \t]*\([ \t]*["']HOME["'][ \t]*\)[ \t]*\.\.[ \t]*)?'''
                  r'''(["'])([^"'\n]*)\4''')


def loaded(text, start, home, hypr):
    """The file a dofile/loadfile/require call at `start` loads, or None when its name is computed."""
    match = LOAD.match(text, start)
    if not match or (match[2] and not re.compile(r'[ \t]*\)').match(text, match.end())):
        return None
    name = match[5]
    if match[1] == 'require':
        # Hyprland looks modules up next to hyprland.lua: ?.lua, then ?/init.lua.
        base = hypr / name.replace('.', '/')
        module = Path(str(base) + '.lua')
        return None if match[3] else module if module.is_file() or not (base / 'init.lua').is_file() else base / 'init.lua'
    if match[3]:
        return Path(str(home) + name) if name.startswith('/') else None
    return Path(name) if name.startswith('/') else None


def config_files(config, home, report=print):
    """hyprland.lua and the files it loads from its folder, in load order: {path: text}. NEKRYPTA's own files are left
    out, and a file that can't be followed is reported as not checked for clashes."""
    hypr, files = config.parent, {}

    def visit(path):
        files[path] = text = path.read_text()
        for match in re.finditer(r'(?<![\w.:])(?:dofile|loadfile|require)\b', live(text)):
            target = loaded(text, match.start(), home, hypr)
            if target and target.resolve().parent == hypr.resolve() and target.name in OWN:
                continue
            if target and target.is_file() and target.resolve().is_relative_to(hypr.resolve()):
                if target.resolve() not in {seen.resolve() for seen in files}:
                    visit(target)
                continue
            why = ('its name is computed' if target is None else f'{shown(target, home)} does not exist' if not target.is_file()
                   else f'{shown(target, home)} is outside {shown(hypr, home)}')
            report(f'Shortcut clashes not checked in the file loaded on line {text.count(chr(10), 0, match.start()) + 1} '
                   f'of {shown(path, home)}: {why}.')
    visit(config)
    return files


def unintegrate(text, before=''):
    """Undo an install's edits to a config file the user has changed since: the NEKRYPTA blocks it added go, and the
    lines it commented out come back. What `before` (the file before that install) already had stays."""
    for marker, name in BLOCKS:
        if marker in before:
            continue
        for loader in (LOADER, OLD_LOADER):
            block = marker + '\n' + loader.format(name)
            text = text.replace('\n\n' + block + '\n', '\n').replace(block + '\n', '').replace(block, '')
    earlier = set(before.split('\n'))
    return '\n'.join(line[len(REPLACED):] if line.startswith(REPLACED) and line not in earlier else line
                     for line in text.split('\n'))


# ---- Choosing what to replace -------------------------------------------------------------------

def label(keys):
    return '+'.join(',' if part == 'COMMA' else part.capitalize() for part in keys.replace(' ', '').split('+'))


def replace_all(spec, existing):
    return True


def skip_all(spec, existing):
    return False


def ask(read=input, write=print, answers=None):
    """Ask about each clash; with `answers`, each answer is saved in it as 'replace' or 'keep'."""
    def decide(spec, existing):
        keys, _, nekrypta = spec
        write(f'{label(keys)} is bound to: {existing[0]}')
        while True:
            answer = read(f'  Replace it with {nekrypta}? [r]eplace / [s]kip: ').strip().lower()
            if answer in ('r', 'replace', 'y', 'yes', 's', 'skip', 'n', 'no', ''):
                replace = answer in ('r', 'replace', 'y', 'yes')
                if answers is not None:
                    answers[normalize(keys)] = 'replace' if replace else 'keep'
                return replace
    return decide


def clash(spec, existing):
    """A preview's decision: list the clash, and show the plan as if it were replaced."""
    print(f'Shortcut clash: {label(spec[0])} is bound to {existing[0]} (--apply asks; --yes replaces)')
    return True


def load(text, marker, name):
    """`text` loading ~/.config/hypr/<name> once, under `marker`; an earlier install's bare dofile is made safe."""
    old = marker + '\n' + OLD_LOADER.format(name)
    if old in text:
        return text.replace(old, marker + '\n' + LOADER.format(name))
    if marker in text:
        return text
    return text.rstrip() + '\n\n' + marker + '\n' + LOADER.format(name) + '\n'


def integrate(files, wanted=SHORTCUTS, decide=replace_all, shell=True, report=print):
    """Config files ({path: text}, hyprland.lua first) with the binds that clash with `wanted` commented out and
    NEKRYPTA's files loaded once from hyprland.lua: (files, chosen specs, skipped specs)."""
    found = {}
    for path, text in files.items():
        for key, spans in conflicts(text, wanted).items():
            found.setdefault(key, []).extend((path, start, end, action) for start, end, action in spans)
    chosen, skipped, cut = [], [], {}
    for spec in wanted:
        existing = found.get(normalize(spec[0]), [])
        if existing and not decide(spec, [action for *_, action in existing]):
            skipped.append(spec)
            continue
        chosen.append(spec)
        for path, start, end, _ in existing:
            cut.setdefault(path, []).append((start, end))
    result = {}
    for path, text in files.items():
        more, kept = dependents(text, cut.get(path, []))
        for line in kept:
            report(f'{path.name} still uses a binding NEKRYPTA replaced, so Hyprland reports an error there until you change it: {line}')
        result[path] = comment_out(text, cut.get(path, []) + more)
    config = next(iter(result))
    for marker, name in BLOCKS:
        if marker in result[config] or (shell if marker == MARKER else WALLPAPER in chosen):
            result[config] = load(result[config], marker, name)
    return result, chosen, skipped


def nekrypta_lua(chosen):
    """integration/nekrypta.lua with only the shortcuts this install binds."""
    template = (ROOT / 'integration/nekrypta.lua').read_text()
    rows = ''.join(f'    ["{keys}"] = "{action}",\n' for keys, action, _ in chosen)
    rendered, count = re.subn(r'local shortcuts = \{\n.*?\n\}', lambda _: 'local shortcuts = {\n' + rows + '}',
                              template, count=1, flags=re.S)
    if count != 1:
        raise ValueError('integration/nekrypta.lua has no shortcuts table')
    return rendered


# ---- Checks before installing ------------------------------------------------------------------

def quickshell_version(run=subprocess.run):
    try:
        output = run(['quickshell', '--version'], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r'(\d+)\.(\d+)\.(\d+)', output)
    return tuple(map(int, match.groups())) if match else None


def hyprland_version(run=subprocess.run, environ=os.environ):
    """Hyprland's version: the running one's (hyprctl), else the installed one's; None when neither says."""
    commands = [['Hyprland', '--version']]
    if environ.get('HYPRLAND_INSTANCE_SIGNATURE'):
        commands.insert(0, ['hyprctl', 'version', '-j'])
    for command in commands:
        try:
            output = run(command, capture_output=True, text=True, timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        try:
            data = json.loads(output)
            output = str(data.get('version') or data.get('tag') or '') if isinstance(data, dict) else output
        except ValueError:
            pass
        match = re.search(r'(\d+)\.(\d+)(?:\.(\d+))?', output)
        if match:
            return tuple(int(part or 0) for part in match.groups())
    return None


def preflight(home, which=shutil.which, version=quickshell_version, module=__import__, processes=set, path=None,
              hyprland=hyprland_version):
    """(missing required packages, missing optional packages, warnings)."""
    def present(name):
        if name.startswith('python:'):
            try:
                module(name.split(':', 1)[1])
                return True
            except ImportError:
                return False
        return bool(which(name))
    required = sorted({package for name, package in REQUIRED.items() if not present(name)})
    optional = sorted({package for name, package in OPTIONAL.items() if not present(name)})
    if not any(which(name) for name in system.WALLPAPER_DAEMONS):
        optional.append('hyprpaper')
    warnings = []
    found = version() if 'quickshell' not in required else None
    if found and found < MINIMUM_QUICKSHELL:
        warnings.append(f'Quickshell {".".join(map(str, found))} is older than {".".join(map(str, MINIMUM_QUICKSHELL))}; update it first.')
        required.append('quickshell')
    if 'hyprland' not in required:
        found, minimum = hyprland(), '.'.join(map(str, MINIMUM_HYPRLAND))
        if found is None:
            warnings.append(f'Could not tell which Hyprland version is installed; NEKRYPTA needs {minimum} or newer.')
        elif found < MINIMUM_HYPRLAND:
            warnings.append(f'Hyprland {".".join(map(str, found))} is older than {minimum}; update it first.')
            required.append('hyprland')
    running = sorted(set(OTHER_SHELLS) & processes())
    if running:
        warnings.append('Running now: ' + ', '.join(running) + '. Remove them from your autostart: NEKRYPTA is the bar and '
                        'the notification daemon, and two of either conflict.')
    entries = [Path(entry).expanduser() for entry in (os.environ.get('PATH', '') if path is None else path).split(':') if entry]
    if home / '.local/bin' not in entries:
        warnings.append('~/.local/bin is not on your PATH, so run the nekrypta command as ~/.local/bin/nekrypta. '
                        'Keybindings use the full path and work either way.')
    return required, optional, warnings


def running_processes():
    try:
        result = subprocess.run(['ps', '-u', str(os.getuid()), '-o', 'comm='], capture_output=True, text=True)
    except OSError:
        return set()
    return set(result.stdout.split())


# ---- Packages and services (./install.sh) -------------------------------------------------------

def privileged(exists=os.path.exists, note=print):
    """How to run a command as root: the first of ROOT_TOOLS that is installed."""
    for tool in ROOT_TOOLS:
        if exists(tool):
            if tool != ROOT_TOOLS[0]:
                note(f'  sudo is not installed, so this step uses {os.path.basename(tool)}.')
            return [tool]
    raise ValueError('No sudo, doas or pkexec is installed. As root, install sudo (pacman -S sudo) and add yourself '
                     'to the wheel group, or install the packages yourself and run ./install.sh --no-packages.')


def confirm(question, read=input, default=True):
    answer = read(question + (' [Y/n] ' if default else ' [y/N] ')).strip().lower()
    return default if not answer else answer in ('y', 'yes')


# The example config's app menu (Super+R) is hyprlauncher, which isn't installed; the copy ./install.sh makes opens
# NEKRYPTA's launcher instead, and --uninstall puts it back.
EXAMPLE_MENU = re.compile(r'^(local menu\s*=\s*)"hyprlauncher"', re.M)
NEKRYPTA_MENU = r'\1os.getenv("HOME") .. "/.local/bin/nekrypta launcher"'


def example_menu():
    """(the example config's app menu line, that line as ./install.sh's copy has it)."""
    try:
        lines = system.EXAMPLE_CONFIG.read_text().split('\n')
    except OSError:
        lines = []
    line = next((line for line in lines if EXAMPLE_MENU.match(line)), 'local menu        = "hyprlauncher"')
    return line, EXAMPLE_MENU.sub(NEKRYPTA_MENU, line, count=1)


def setup_system(home, apply=False, yes=False, interactive=True, read=input, run=subprocess.run, detected=None, login_screen=True):
    """Install the packages and enable the services this machine needs, after showing why. Without `apply`, only
    show them. Returns the plan."""
    needs = detected if detected is not None else system.plan(home)
    print('Checking this machine')
    for note in needs['notes']:
        print('  note: ' + note)
    boot = needs.get('boot', [])
    # The login screen is its own choice (some people prefer typing start-hyprland on a TTY), asked
    # before the summary so the list confirmed below is what gets installed.
    if 'sddm' in boot and (not login_screen or (apply and interactive and not yes and not confirm(
            '  No login screen yet. Add SDDM? Without it, log in on a TTY and run start-hyprland.', read))):
        needs = dict(needs, packages=[(n, r) for n, r in needs['packages'] if n != 'sddm'])
        boot = [unit for unit in boot if unit != 'sddm']
    if not needs['packages'] and not needs['services'] and not boot and not needs['config']:
        print('  Everything NEKRYPTA needs is already installed.\n')
        return needs
    if needs['packages']:
        print(f'  Packages to install ({len(needs["packages"])}):')
        width = max(len(name) for name, _ in needs['packages'])
        for name, reason in needs['packages']:
            print(f'    {name.ljust(width)}  {reason}')
    if needs['services']:
        print('  Services to enable: ' + ', '.join(needs['services']))
    if boot:
        print('  Enabled from the next boot: ' + ', '.join(boot))
    if needs['config']:
        print("  Hyprland config: none yet, so Hyprland's example becomes ~/.config/hypr/hyprland.lua")
    if not apply:
        print('  Preview only: with --apply, these are installed after you confirm.\n')
        return needs
    # --noconfirm takes pacman's default answers, and replacing a conflicting package defaults to no, which stops
    # the whole install. So pacman asks those itself, even with --yes.
    conflicts = needs.get('conflicts', [])
    if not yes and not interactive:
        raise ValueError('not a terminal, so nothing was installed. Pass --yes to install these, or --no-packages to skip them.')
    if conflicts and not interactive:
        raise ValueError(f'pacman has to ask before it replaces {", ".join(conflicts)}, and this is not a terminal. '
                         'Run ./install.sh in a terminal.')
    if not yes and not confirm('\nGo ahead?', read):
        raise ValueError('nothing was installed. Run ./install.sh --no-packages to install only the shell files.')
    root = privileged() if needs['packages'] or needs['services'] or boot else []
    sys.stdout.flush()  # keep this output ahead of pacman's when piped

    def step(command):
        """Run one pacman or systemctl step. Ctrl+C there can leave part of it done, so it isn't reported as
        "nothing was written"."""
        try:
            return run(command).returncode == 0
        except KeyboardInterrupt:
            raise ValueError(f'cancelled while {os.path.basename(command[len(root)])} was running, so part of this step '
                             'may be done. Run ./install.sh again to finish.') from None
    if needs['packages']:
        command = root + [PACMAN, '-S', '--needed'] + (['--noconfirm'] if yes and not conflicts else []) + [name for name, _ in needs['packages']]
        if not step(command):
            raise ValueError('pacman did not finish; nothing else was changed. If it could not download a package, '
                             'your package lists are out of date: run sudo pacman -Syu, then ./install.sh again.')
        remember_system(home, packages=[name for name, _ in needs['packages']])
    if needs['services']:
        if not step(root + [SYSTEMCTL, 'enable', '--now'] + [unit + '.service' for unit in needs['services']]):
            raise ValueError('could not enable ' + ', '.join(needs['services']) + '.')
        remember_system(home, services=needs['services'])
    if boot:
        # Not started now: a login screen would take over this console in the middle of the install.
        if not step(root + [SYSTEMCTL, 'enable'] + [unit + '.service' for unit in boot]):
            raise ValueError('could not enable ' + ', '.join(boot) + '.')
        remember_system(home, boot=boot)
    if needs['config']:
        config = home / '.config/hypr/hyprland.lua'
        config.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(config, EXAMPLE_MENU.sub(NEKRYPTA_MENU, system.EXAMPLE_CONFIG.read_text(), count=1).encode())
        remember_system(home, config=[str(config)])
        print(f"Copied Hyprland's example config to {shown(config, home)}; its keyboard layout is US (kb_layout).")
    if 'sddm' in boot:
        print('After the install, reboot and choose Hyprland on the login screen.')
    print()
    return needs


# ---- Planning and writing -----------------------------------------------------------------------

def hyprland_config(home):
    config = home / '.config/hypr/hyprland.lua'
    if config.is_file():
        return config
    if (home / '.config/hypr/hyprland.conf').is_file():
        raise ValueError('NEKRYPTA needs Hyprland\'s Lua configuration (~/.config/hypr/hyprland.lua), but this setup uses '
                         'hyprland.conf. See "Requirements" in the README.')
    raise ValueError('No ~/.config/hypr/hyprland.lua yet. Start from Hyprland\'s example:\n'
                     '  mkdir -p ~/.config/hypr && cp /usr/share/hypr/hyprland.lua ~/.config/hypr/')


def check_config_format(home, shell_only=False, wallpaper_shortcut=False):
    """Stop before any package is installed when the install would later stop at a hyprland.conf: NEKRYPTA's
    shortcuts go into hyprland.lua. Without either file, ./install.sh copies Hyprland's example first."""
    hypr = home / '.config/hypr'
    if (not shell_only or wallpaper_shortcut) and not (hypr / 'hyprland.lua').exists() and (hypr / 'hyprland.conf').exists():
        hyprland_config(home)  # raises, saying what to do


def payload(home, shell_only=False, wallpaper_shortcut=False, decide=replace_all, report=print, kept=()):
    """Every file this install writes: {path: (bytes, mode)}. Clashes on `kept` keys (normalized) keep the user's
    binding without asking."""
    targets = {}
    shell = home / '.config/quickshell/nekrypta'
    for item in ROOT.rglob('*'):
        relative = item.relative_to(ROOT)
        if any(p.startswith('.') or p == '__pycache__' for p in relative.parts):
            continue
        if item.is_file() and (relative.parts[0] in ('core', 'components', 'features', 'services', 'helpers', 'assets')
                               or str(relative) == 'scripts/session-action' or item.name in ('shell.qml', 'qmldir')):
            mode = 0o755 if str(relative) == 'scripts/session-action' else item.stat().st_mode & 0o777
            targets[shell / relative] = (item.read_bytes(), mode)
    # What `nekrypta --version` shows; a copy without git history installs without one.
    commit = version()
    if commit != 'unknown':
        targets[shell / 'COMMIT'] = ((commit + '\n').encode(), 0o644)
    targets[home / '.local/bin/nekrypta'] = ((ROOT / 'scripts/nekrypta').read_bytes(), 0o755)
    wanted = ([] if shell_only else SHORTCUTS) + ([WALLPAPER] if wallpaper_shortcut else [])
    if shell_only and not wallpaper_shortcut:
        return targets
    config = hyprland_config(home)
    files = config_files(config, home, report)
    kept = set(kept)
    texts, chosen, skipped = integrate(files, wanted, lambda spec, existing: normalize(spec[0]) not in kept and decide(spec, existing),
                                       shell=not shell_only, report=report)
    earlier = [spec for spec in skipped if normalize(spec[0]) in kept]
    for keys, action, nekrypta in skipped:
        if (keys, action, nekrypta) not in earlier:
            report(f'Kept your {label(keys)} binding. {nekrypta} has no shortcut; its keys are fixed in core/personal.json.')
    if earlier:
        report(f'Kept your {", ".join(label(keys) for keys, *_ in earlier)} binding{"s" if len(earlier) > 1 else ""}, as you '
               'chose before; add --ask-again to be asked again.')
    if WALLPAPER in chosen:
        targets[home / '.config/hypr/nekrypta-wallpaper.lua'] = ((ROOT / 'integration/nekrypta-wallpaper.lua').read_bytes(), 0o644)
    if not shell_only:
        targets[home / '.config/hypr/nekrypta.lua'] = (nekrypta_lua([spec for spec in chosen if spec != WALLPAPER]).encode(), 0o644)
        for name in ('hypridle.conf', 'hyprlock.conf'):
            # Separate filenames keep pre-existing lock/idle configuration available.
            targets[home / '.config/hypr' / ('nekrypta-' + name)] = ((ROOT / 'integration' / name).read_bytes(), 0o644)
        targets[home / '.config/systemd/user/nekrypta.service'] = ((ROOT / 'integration/nekrypta.service').read_bytes(), 0o644)
    unchecked = False
    # hyprland.lua last, so a rollback (which goes backwards) handles it before the files it loads.
    for path in [*list(texts)[1:], config]:
        changed = texts[path] != files[path]
        if not changed and path != config:
            continue
        verdict = lua_ok(texts[path]) if changed else True
        unchecked = unchecked or verdict is None
        if verdict is False and lua_ok(files[path]):
            raise ValueError(f'Editing {path} would break its Lua syntax, so nothing was changed. Please report this with your config.')
        targets[path] = (texts[path].encode(), path.stat().st_mode & 0o777)
    if unchecked:
        report('luac (from the lua package) is not installed, so the edited Hyprland config was not syntax-checked.')
    return targets


def real(path, home):
    """Where a write really lands: symlinks (stow and similar) are followed, but only within home."""
    resolved = path.resolve()
    if not resolved.is_relative_to(home.resolve()):
        raise ValueError(f'{path} points outside your home directory ({resolved}); refusing to write there.')
    return resolved


def stale(home, targets, record):
    """Files NEKRYPTA installed in its shell folder that this version no longer ships. Other files there stay."""
    shell = real(home / '.config/quickshell/nekrypta', home)
    shipped = {real(path, home) for path in targets}
    # Only canonical paths: a record naming "nekrypta/../x" or a path through a symlink can't reach outside.
    return sorted(path for path in map(Path, record)
                  if path.is_absolute() and path.resolve() == path and path.is_relative_to(shell) and path not in shipped and path.is_file())


def shown(path, home):
    return '~/' + str(path.relative_to(home)) if path.is_relative_to(home) else str(path)


def status(path, data, mode):
    if not path.exists():
        return 'new'
    return 'unchanged' if path.is_file() and path.read_bytes() == data and path.stat().st_mode & 0o777 == mode else 'changed'


def report_files(home, resolved, removed, verbose=False):
    """Config files one by one; the shell's own files as a count unless --verbose."""
    shell = real(home / '.config/quickshell/nekrypta', home)
    counts = {'new': 0, 'changed': 0, 'unchanged': 0}
    print('Files')
    for path, (data, mode) in resolved.items():
        state = status(path, data, mode)
        if path.is_relative_to(shell) and not verbose:
            counts[state] += 1
        elif state != 'unchanged' or verbose:
            print(f'  {"create" if state == "new" else "update" if state == "changed" else "same  "}  {shown(path, home)}')
    for path in removed:
        if verbose or not path.is_relative_to(shell):
            print(f'  remove  {shown(path, home)}')
    if (not verbose and any(counts.values())) or removed:
        parts = [f'{n} {state}' for state, n in counts.items() if n] + ([f'{len(removed)} removed'] if removed else [])
        print(f'  shell   {shown(shell, home)}: ' + ', '.join(parts))


def check_location(home):
    """A checkout inside the folder it installs to (or around it) would copy over and delete itself."""
    shell, root = (home / '.config/quickshell/nekrypta').resolve(), ROOT.resolve()
    if root.is_relative_to(shell) or shell.is_relative_to(root):
        raise ValueError(f'This copy of NEKRYPTA ({root}) overlaps the folder it installs to ({shell}). Clone it somewhere '
                         'else, for example ~/src/nekrypta, and run ./install.sh from there.')


def own_home(home):
    """Whether `home` is this user's own, so the running session (user services, processes) belongs to it. For any
    other --home only files change."""
    return home.resolve() == Path.home().resolve()


def session_skipped(home):
    print(f'Service steps skipped: {home} is not your home directory, so your running session is left alone.')


# ---- What NEKRYPTA keeps in ~/.local/state/nekrypta ---------------------------------------------------
# backups/<time>/   what each --apply replaced (for --rollback)
# originals/        each file as it was before NEKRYPTA first wrote it; never replaced (--uninstall drops what it restores)
# installed.json    every file NEKRYPTA has installed, with the hash of what it wrote
# choices.json      the answer to each shortcut clash, so a binding the user kept isn't asked about again
# system.json       packages, services and the Hyprland config ./install.sh added, for --uninstall to list

def remember_system(home, **added):
    path = home / '.local/state/nekrypta/system.json'
    try:
        record = json.loads(path.read_text())
    except (OSError, ValueError):
        record = {'version': 1}
    for key, values in added.items():
        record[key] = list(dict.fromkeys(record.get(key, []) + list(values)))
    atomic_write(path, json.dumps(record, indent=2).encode(), 0o600)


def choices(home):
    """{normalized keys: 'keep' or 'replace'}: what the user answered about each clash."""
    try:
        return dict(json.loads((home / '.local/state/nekrypta/choices.json').read_text())['shortcuts'])
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def save_choices(home, shortcuts):
    path = home / '.local/state/nekrypta/choices.json'
    data = json.dumps({'version': 1, 'shortcuts': shortcuts}, indent=2, sort_keys=True).encode()
    if not path.is_file() or path.read_bytes() != data:
        atomic_write(path, data, 0o600)


def backup_manifests(home):
    """(folder, manifest) of every backup of this home, oldest first."""
    found = []
    # Folder names start with the time to the second; the manifest's age orders two from the same second.
    for path in sorted((home / '.local/state/nekrypta/backups').glob('*/manifest.json'),
                       key=lambda path: (path.parent.name[:15], path.stat().st_mtime_ns)):
        try:
            manifest = json.loads(path.read_text())
            if Path(manifest['home']).resolve() == home.resolve():
                found.append((path.parent, manifest))
        except (OSError, ValueError, KeyError):
            continue
    return found


def installed(home):
    """{path: sha256} of the files NEKRYPTA has installed. For installs older than this record, their backups say."""
    try:
        return json.loads((home / '.local/state/nekrypta/installed.json').read_text())['files']
    except FileNotFoundError:
        pass
    record = {}
    for _, manifest in backup_manifests(home):
        record.update((entry['path'], entry['installed_sha256']) for entry in manifest['files'] if not entry.get('removed'))
    return record


def save_record(home, record):
    path = home / '.local/state/nekrypta/installed.json'
    data = json.dumps({'version': 1, 'home': str(home), 'files': record}, indent=2, sort_keys=True).encode()
    if not path.is_file() or path.read_bytes() != data:
        atomic_write(path, data, 0o600)


def first_states(home):
    """(backup folder, entry) for every file in the backups, oldest first: the first entry of a path is that file
    before any backed-up install wrote it."""
    return [(state, entry) for state, data in backup_manifests(home) for entry in data['files'] if not entry.get('removed')]


def originals(home):
    """{path: (bytes or None if it didn't exist, mode)}: each file as it was before NEKRYPTA first wrote it. Without
    the originals folder (installs older than it), the oldest backups say."""
    folder = home / '.local/state/nekrypta/originals'
    try:
        sources = [(folder, entry) for entry in json.loads((folder / 'manifest.json').read_text())['files']]
    except FileNotFoundError:
        sources = first_states(home)
    found = {}
    for where, entry in sources:
        if entry['path'] not in found:
            try:
                found[entry['path']] = (None if entry['backup'] is None else member(where, entry['backup']).read_bytes(), entry['mode'])
            except OSError:
                continue
    return found


def keep_originals(home, paths):
    """Save how each of `paths` is now, unless NEKRYPTA saved it before: ~/.local/state/nekrypta/originals has every file
    as it was before NEKRYPTA first wrote it. Without that folder yet, it starts from the oldest backups."""
    folder = home / '.local/state/nekrypta/originals'
    try:
        manifest, sources = json.loads((folder / 'manifest.json').read_text()), []
    except FileNotFoundError:
        manifest, sources = {'version': 1, 'home': str(home), 'files': []}, first_states(home)
    known, count = {entry['path'] for entry in manifest['files']}, len(manifest['files'])

    def keep(path, data, mode):
        entry = {'path': path, 'backup': None, 'mode': mode}
        if data is not None:
            index = len(manifest['files'])
            while (folder / str(index)).exists():  # names --uninstall freed can sit below files still in use
                index += 1
            entry['backup'] = str(index)
            atomic_write(folder / entry['backup'], data, 0o600)
        manifest['files'].append(entry)
        known.add(path)
    for state, entry in sources:
        if entry['path'] not in known:
            try:
                keep(entry['path'], None if entry['backup'] is None else member(state, entry['backup']).read_bytes(), entry['mode'])
            except OSError:
                continue
    for path in paths:
        if str(path) not in known:
            keep(str(path), path.read_bytes() if path.exists() else None, path.stat().st_mode & 0o777 if path.exists() else None)
    if len(manifest['files']) != count:
        atomic_write(folder / 'manifest.json', json.dumps(manifest, indent=2).encode(), 0o600)


def backup(home, changes, removed):
    """A backup folder with what these writes replace and these removals delete, for --rollback."""
    backups = home / '.local/state/nekrypta/backups'
    backups.mkdir(parents=True, exist_ok=True)
    state = Path(tempfile.mkdtemp(prefix=time.strftime('%Y%m%d-%H%M%S-'), dir=backups))
    manifest = {'version': 2, 'home': str(home), 'files': []}
    try:
        for index, (path, (data, mode)) in enumerate(changes.items()):
            entry = {'path': str(path), 'backup': None, 'mode': mode, 'installed_sha256': digest(data)}
            if path.exists():
                entry.update(backup=str(index), mode=path.stat().st_mode & 0o777)
                (state / entry['backup']).write_bytes(path.read_bytes())
            manifest['files'].append(entry)
        for index, path in enumerate(removed, start=len(changes)):
            try:
                mode = path.stat().st_mode & 0o777
                (state / str(index)).write_bytes(path.read_bytes())
            except FileNotFoundError:
                continue  # deleted since the plan was made: nothing to keep
            manifest['files'].append({'path': str(path), 'backup': str(index), 'mode': mode, 'removed': True})
        atomic_write(state / 'manifest.json', json.dumps(manifest, indent=2).encode(), 0o600)
    except BaseException:
        shutil.rmtree(state, ignore_errors=True)  # a half-written backup can't be rolled back to
        raise
    return state


def undo_command(state, home):
    return f'python3 {shown(ROOT / "scripts/install.py", home)} --rollback {shown(state, home)} --apply'


# ---- Installing and rolling back ----------------------------------------------------------------

def install(home, apply=False, shell_only=False, wallpaper_shortcut=False, yes=False, force=False,
            interactive=None, read=input, checks=None, verbose=False, ask_again=False, packages=(), hints=True):
    """Install NEKRYPTA, or with apply=False show what that would change. `packages` are more missing packages to
    mention (an update's). Returns whether anything was written."""
    if not (ROOT / 'shell.qml').exists():
        raise ValueError('Missing shell.qml; installation is incomplete')
    check_location(home)
    required, optional, warnings = checks() if checks else preflight(home, processes=running_processes)
    extra = [name for name in packages if name not in required and name not in optional]
    for warning in warnings:
        print('Warning: ' + warning)
    if optional:
        print('Optional, for every feature: sudo pacman -S --needed ' + ' '.join(optional))
    if extra:
        print('Not installed yet (./install.sh adds them): sudo pacman -S --needed ' + ' '.join(extra))
    if required:
        print('Required: sudo pacman -S --needed ' + ' '.join(required) + '  (or run ./install.sh, which installs them)')
        if apply and not force:
            raise ValueError('install the required packages first, or pass --force to install anyway')
    if warnings or optional or extra or required:
        print()
    earlier, answers = {} if ask_again else choices(home), {}
    if not apply:
        decide = clash  # a preview lists clashes and shows the full plan; --apply asks unless --yes
    elif yes:
        decide = replace_all
    elif sys.stdin.isatty() if interactive is None else interactive:
        decide = ask(read, answers=answers)
    else:
        print('Not a terminal, so your bindings that clash with NEKRYPTA shortcuts are kept (pass --yes to replace them).')
        decide = skip_all
    targets = payload(home, shell_only=shell_only, wallpaper_shortcut=wallpaper_shortcut, decide=decide,
                      kept=[keys for keys, choice in earlier.items() if choice == 'keep'])
    resolved = {real(path, home): value for path, value in targets.items()}
    record = installed(home)
    removed = stale(home, targets, record)
    report_files(home, resolved, removed, verbose)
    if not apply:
        print('\nPreview only. Re-run with --apply to write these files. No services are started.')
        return
    changes = {path: value for path, value in resolved.items() if status(path, *value) != 'unchanged'}
    for path in changes:
        if path.exists() and not path.is_file():
            raise ValueError(f'Not a regular file: {path}')
    state = None
    if changes or removed:
        keep_originals(home, [*changes, *removed])
        state = backup(home, changes, removed)
        try:
            for path, (data, mode) in changes.items():
                atomic_write(path, data, mode)
            for path in removed:
                path.unlink(missing_ok=True)
        except BaseException as exc:
            print(f'\nStopped partway, so only some files were written. Everything they replaced is in {shown(state, home)}.\n'
                  f'Undo with: {undo_command(state, home)}')
            if isinstance(exc, KeyboardInterrupt):
                raise ValueError('cancelled while writing') from None
            raise
    for path in removed:
        record.pop(str(path), None)
    record.update((str(path), digest(data)) for path, (data, _) in resolved.items())
    save_record(home, record)
    if answers or ask_again:
        save_choices(home, {**earlier, **answers})
    if state:
        print(f'\nInstalled NEKRYPTA {version()}. Backup of everything replaced: {shown(state, home)}')
        print(f'Undo with: {undo_command(state, home)}')
    else:
        print('\nEverything is up to date; nothing was written.')
    if not hints:
        return bool(state)
    if os.environ.get('HYPRLAND_INSTANCE_SIGNATURE'):
        print('\nStart it now:\n  ~/.local/bin/nekrypta start && hyprctl reload')
    elif system.service_state('display-manager'):
        print('\nNext: reboot (or log out) and choose Hyprland on the login screen. NEKRYPTA starts with it.')
    else:
        print('\nNext: log in on a TTY and run start-hyprland. NEKRYPTA starts with Hyprland.')
    return bool(state)


def member(folder, name):
    """A saved file in a backup folder, by the name the installer gave it: its number in the manifest. Any other
    name (a path, "..", a symlink) means the manifest wasn't written by NEKRYPTA, and nothing is read or removed."""
    path = folder / name if isinstance(name, str) and re.fullmatch(r'[0-9]+', name) else None
    if path is None or path.is_symlink():
        raise ValueError(f'Not a NEKRYPTA backup: unexpected file {name!r} in {folder}')
    return path


def atomic_write(path, data, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.nekrypta-')
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def undo_edits(path, current, before):
    """A Hyprland config file changed since the install, without what the install added; None if there's nothing to
    undo or the result wouldn't parse."""
    if current is None or path.suffix != '.lua' or path.name in OWN:
        return None
    text = current.decode()
    undone = unintegrate(text, before.decode() if before is not None else '')
    if undone == text or (lua_ok(text) and lua_ok(undone) is False):
        return None
    return undone


def still_loads(home, name, texts):
    """Whether hyprland.lua, as this rollback leaves it, still runs an earlier install's bare dofile of `name`, which
    fails on every Hyprland start once the file is gone."""
    config = (home / '.config/hypr/hyprland.lua').resolve()
    text = texts[config] if config in texts else config.read_text() if config.is_file() else None
    return bool(text) and any(line.strip() == OLD_LOADER.format(name) for line in text.split('\n'))


def rollback(home, state, apply=False):
    """Undo one --apply from its backup folder. Files changed since are left alone, except Hyprland config files,
    which only lose what that install added. Returns how many files were left alone."""
    manifest = json.loads((state / 'manifest.json').read_text())
    if Path(manifest['home']).resolve() != home.resolve():
        raise ValueError('Backup belongs to a different home directory')
    record, texts, skipped = installed(home), {}, 0
    for entry in reversed(manifest['files']):
        path = Path(entry['path'])
        if real(path, home) != path:
            raise ValueError(f'Unsafe restore path: {path}')
        before = None if entry['backup'] is None else member(state, entry['backup']).read_bytes()
        if entry.get('removed'):
            if path.exists():
                print(f'SKIP exists again: {path}')
                skipped += 1
                continue
            print(f'RESTORE {path}')
            if apply:
                atomic_write(path, before, entry['mode'])
            record[str(path)] = digest(before)  # NEKRYPTA's again, so a later install or uninstall handles it
            continue
        current = path.read_bytes() if path.is_file() else None
        if current is not None and digest(current) == entry['installed_sha256']:
            if before is None and path.name in OWN and still_loads(home, path.name, texts):
                print(f'SKIP still loaded by hyprland.lua: {path}')
                skipped += 1
                continue
            print(f'{"REMOVE" if before is None else "RESTORE"} {path}')
            if apply:
                path.unlink() if before is None else atomic_write(path, before, entry['mode'])
            if path.suffix == '.lua':
                texts[path] = None if before is None else before.decode(errors='replace')
            if before is None:
                record.pop(str(path), None)
            else:
                record[str(path)] = digest(before)
            continue
        undone = undo_edits(path, current, before)
        if undone is None:
            print(f'SKIP changed or missing: {path}')
            skipped += 1
            continue
        print(f"UNDO NEKRYPTA's lines in {path} (edited since the install)")
        if apply:
            atomic_write(path, undone.encode(), path.stat().st_mode & 0o777)
        texts[path] = undone
    if apply:
        save_record(home, record)
    else:
        print('Preview only. Add --apply to restore unchanged installed files.')
    return skipped


# ---- Uninstalling ------------------------------------------------------------------

def without_nekrypta(current, original):
    """A Hyprland config file without what NEKRYPTA added: ('restore', original) when nothing else (but blank space)
    differs from how it was before NEKRYPTA, ('undo', text) keeping later edits, (None, None) when no NEKRYPTA lines are
    left, or ('skip', None) when the result wouldn't parse."""
    text = current.decode(errors='replace')
    before = original.decode(errors='replace') if original is not None else ''
    undone = unintegrate(text, before)
    if original is not None and undone.split() == before.split():
        return 'restore', original
    if undone == text:
        return None, None
    if lua_ok(text) and lua_ok(undone) is False:
        return 'skip', None
    return 'undo', undone.encode()


def quietly(run, command):
    try:
        return run(command, capture_output=True).returncode == 0
    except OSError:
        return False


def drop_originals(home, paths):
    """Forget the originals of `paths` (they're back in place), so a later install keeps their state then."""
    folder = home / '.local/state/nekrypta/originals'
    manifest = json.loads((folder / 'manifest.json').read_text())
    dropped = [entry for entry in manifest['files'] if entry['path'] in paths]
    manifest['files'] = [entry for entry in manifest['files'] if entry['path'] not in paths]
    atomic_write(folder / 'manifest.json', json.dumps(manifest, indent=2).encode(), 0o600)
    for entry in dropped:
        if entry['backup'] is not None:
            member(folder, entry['backup']).unlink(missing_ok=True)


def uninstall(home, apply=False, purge=False, yes=False, interactive=None, read=input, run=subprocess.run, verbose=False):
    """Remove what NEKRYPTA installed and put back what it replaced; with apply=False only show it. Settings and state
    stay unless `purge`. Packages and system services are only listed. Returns how many files were left alone."""
    record, before = installed(home), originals(home)
    shell, hypr = real(home / '.config/quickshell/nekrypta', home), home / '.config/hypr'
    unit, idle = home / '.config/systemd/user/nekrypta.service', hypr / 'nekrypta-hypridle.conf'
    texts, actions, skips, done = {}, [], [], set()

    def config_file(path, current, original, mode):
        verb, data = without_nekrypta(current, original)
        if verb == 'skip':
            skips.append((path, "taking NEKRYPTA's lines out would break its syntax"))
        elif verb is None:
            done.add(str(path))
        else:
            actions.append((verb, path, data, mode if verb == 'restore' else path.stat().st_mode & 0o777))
            texts[path] = data.decode(errors='replace')
    # Files earlier installs edited without a record of it: only their NEKRYPTA blocks and replaced lines change.
    if (hypr / 'hyprland.lua').is_file():
        for path, text in config_files(hypr / 'hyprland.lua', home, report=lambda line: None).items():
            path = path.resolve()
            if str(path) not in record and path.is_relative_to(home.resolve()):
                config_file(path, text.encode(), None, None)
    # Hyprland config files first, so whether hyprland.lua still loads one of NEKRYPTA's files is known before it goes.
    shells = home.resolve() / '.config/quickshell'
    for name in sorted(record, key=lambda name: (not (name.endswith('.lua') and Path(name).name not in OWN), name)):
        path = Path(name)
        try:
            safe = real(path, home) == path
        except ValueError:
            safe = False
        current = path.read_bytes() if safe and path.is_file() else None
        original, mode = before.get(name, (None, None))
        # A shell file that's gone (an earlier shell folder, say) needs nothing; any other file that's gone was
        # there before NEKRYPTA, and is left for the user rather than brought back.
        if not safe or (current is None and (path.exists() or (original is not None and not path.is_relative_to(shells)))):
            skips.append((path, 'now a link' if not safe else 'not a regular file' if path.exists() else 'missing'))
        elif current is None or current == original:
            done.add(name)
        elif path.suffix == '.lua' and path.name not in OWN:
            config_file(path, current, original, mode)
        elif digest(current) != record[name]:
            skips.append((path, 'changed since NEKRYPTA installed it'))
        elif original is None and path.name in OWN and still_loads(home, path.name, texts):
            skips.append((path, 'hyprland.lua still loads it'))
        else:
            actions.append(('remove', path, None, None) if original is None else ('restore', path, original, mode))

    state_folder = home / '.local/state/nekrypta'
    try:
        setup = json.loads((state_folder / 'system.json').read_text())
    except (OSError, ValueError):
        setup = None
    # A hyprland.lua ./install.sh copied from Hyprland's example gets the example's app menu back, unless that line
    # was changed since.
    config, menu = (hypr / 'hyprland.lua').resolve(), False
    if setup and any(Path(name).resolve() == config for name in setup.get('config', [])) and config.is_file() \
            and config not in {path for path, _ in skips}:
        original, rewritten = example_menu()
        lines = (texts[config] if config in texts else config.read_text(errors='replace')).split('\n')
        if rewritten in lines:
            menu, texts[config] = True, '\n'.join(original if line == rewritten else line for line in lines)
            planned = [index for index, (_, path, *_) in enumerate(actions) if path == config]
            if planned:
                verb, _, _, mode = actions[planned[0]]
                actions[planned[0]] = (verb, config, texts[config].encode(), mode)
            else:
                actions.append(('undo', config, texts[config].encode(), config.stat().st_mode & 0o777))
    removing = {path for verb, path, *_ in actions if verb == 'remove'}
    others = sorted(path for path in shell.rglob('*') if path.is_file() and path not in removing
                    and '__pycache__' not in path.relative_to(shell).parts) if shell.is_dir() else []
    data = [(home / folder, what) for folder, what in DATA if (home / folder).exists() or (home / folder).is_symlink()]
    purged = [(folder, what) for folder, what in data if purge and not (skips and folder == state_folder)]
    session = own_home(home)
    print('Uninstalling NEKRYPTA' + ('' if version(shell) == 'unknown' else ' ' + version(shell)) + '\n')
    if not session:
        session_skipped(home)
    elif unit.exists():
        print('Service\n  stop     nekrypta.service, and disable it' + ('\n  stop     NEKRYPTA\'s hypridle, if it runs' if idle.exists() else ''))
    print('Files')
    for verb, path, *_ in actions:
        if verbose or not path.is_relative_to(shell):
            print(f'  {verb.ljust(7)}  {shown(path, home)}' + (": only NEKRYPTA's lines; your later edits stay" if verb == 'undo' else ''))
    inside = sum(path.is_relative_to(shell) for path in removing)
    if inside and not verbose:
        print(f'  remove   {shown(shell, home)}: {inside} files' + ('' if others else ' and the folder'))
    mine = [path for path in others if path not in {path for path, _ in skips}]
    for path in mine[:5]:
        print(f'  keep     {shown(path, home)}: not installed by NEKRYPTA')
    if len(mine) > 5:
        print(f'  keep     {len(mine) - 5} more files in {shown(shell, home)}')
    for path, why in skips:
        print(f'  skip     {shown(path, home)}: {why}')
    if not actions and not skips:
        print('  nothing to remove')
    if purged:
        print('\nDeleted for good (--purge): ' + '; '.join(f'{shown(folder, home)} ({what})' for folder, what in purged))
    kept = [(folder, what) for folder, what in data if (folder, what) not in purged]
    if kept:
        print('\nKept: ' + '; '.join(f'{shown(folder, home)} ({what})' for folder, what in kept)
              + ('. --purge would keep ~/.local/state/nekrypta too, while files are left alone (skip above).' if purge
                 else '. Add --purge to delete them too.'))
    print('\nNot removed; remove these yourself if you no longer want them:')
    if setup is None:
        print('  whatever ./install.sh installed or enabled (this install has no record of it; dependencies.txt lists '
              'what it may have added)')
    else:
        if setup.get('services'):
            print('  services ./install.sh enabled: ' + ', '.join(setup['services']))
            print('    sudo systemctl disable --now ' + ' '.join(unit + '.service' for unit in setup['services']))
        if setup.get('boot'):
            print('  enabled from boot: ' + ', '.join(setup['boot']) + " (the login screen; don't stop it while you're logged in through it)")
            print('    sudo systemctl disable ' + ' '.join(unit + '.service' for unit in setup['boot']))
        if setup.get('packages'):
            print('  packages ./install.sh installed (pacman refuses to remove one that something else still needs):')
            print('    sudo pacman -Rns ' + ' '.join(setup['packages']))
        for name in setup.get('config', []):
            print(f"  {shown(Path(name), home)}, copied from Hyprland's example"
                  + ("; its app menu (Super+R) goes back to the example's hyprlauncher" if menu and Path(name).resolve() == config else ''))
    text = texts[config] if config in texts else config.read_text(errors='replace') if config.is_file() else ''
    for number, line in enumerate(text.split('\n'), start=1):
        if '.local/bin/nekrypta' in line and not line.strip().startswith('--'):
            print(f'  line {number} of {shown(hypr / "hyprland.lua", home)} runs the nekrypta command, which is gone: {line.strip()[:80]}')
    if not apply:
        print('\nPreview only. Add --apply to uninstall.')
        return len(skips)
    if not actions and not unit.exists() and not purged:
        print('\nNEKRYPTA is not installed here; nothing to remove.')
        return len(skips)
    if not yes:
        if not (sys.stdin.isatty() if interactive is None else interactive):
            raise ValueError('not a terminal, so nothing was removed. Pass --yes to uninstall.')
        if not confirm('\nUninstall NEKRYPTA' + (' and delete the folders above for good' if purged else '') + '?', read, default=False):
            raise ValueError('nothing was removed.')

    if session:
        if unit.exists():
            quietly(run, ['systemctl', '--user', 'stop', 'nekrypta.service'])
            quietly(run, ['systemctl', '--user', 'disable', 'nekrypta.service'])
        # The one `nekrypta start` runs; it would go on locking with a command that's gone.
        quietly(run, ['pkill', '-u', str(os.getuid()), '-x', '-f', 'hypridle --config ' + re.sub(r'([][.^$*+?(){}|\\])', r'\\\1', str(idle))])
    keeping_state = not any(folder == state_folder for folder, _ in purged)
    state = None
    if actions and keeping_state:
        keep_originals(home, [])  # an install older than the originals folder: start it from the backups first
        state = backup(home, {path: (data, mode) for verb, path, data, mode in actions if verb != 'remove'}, sorted(removing))
    try:
        for verb, path, data, mode in actions:
            path.unlink(missing_ok=True) if verb == 'remove' else atomic_write(path, data, mode)
    except BaseException:
        if state:
            print(f'\nStopped partway. Everything changed so far is in {shown(state, home)}.')
        raise
    if shell.is_dir():
        for folder in sorted((path for path in shell.rglob('*') if path.is_dir() and not path.is_symlink()),
                             key=lambda path: len(path.parts), reverse=True) + [shell]:
            if folder.name == '__pycache__' and all(item.suffix == '.pyc' and item.is_file() for item in folder.iterdir()):
                shutil.rmtree(folder)
            elif not any(folder.iterdir()):
                folder.rmdir()
        if not shell.exists() and not any(shell.parent.iterdir()):
            shell.parent.rmdir()  # ~/.config/quickshell, when NEKRYPTA was the only shell in it
    if session and unit in removing:
        quietly(run, ['systemctl', '--user', 'daemon-reload'])
    if keeping_state:
        handled = done | {str(path) for _, path, *_ in actions}
        save_record(home, {name: sha for name, sha in record.items() if name not in handled})
        if (state_folder / 'originals/manifest.json').is_file():
            drop_originals(home, handled)
    for folder, _ in purged:
        if folder.is_symlink():
            folder.unlink()
        elif not folder.resolve().is_relative_to(home.resolve()):
            # A symlinked parent (say ~/.cache) leads outside the home: its contents aren't NEKRYPTA's to delete.
            print(f'SKIP outside your home directory: {folder} -> {folder.resolve()}')
        elif folder.is_dir():
            shutil.rmtree(folder)
        elif folder.exists():
            folder.unlink()
    print('\nUninstalled NEKRYPTA.' + (f' Backup of everything it changed or removed: {shown(state, home)}' if state else ''))
    if session and os.environ.get('HYPRLAND_INSTANCE_SIGNATURE'):
        print("Reload Hyprland to drop NEKRYPTA's shortcuts: hyprctl reload")
    return len(skips)


def shell_running(run=subprocess.run):
    return quietly(run, ['systemctl', '--user', 'is-active', '--quiet', 'nekrypta.service'])


def main():
    # ./install.sh runs this with --setup --apply; its --help leaves out what it always passes.
    wrapper = os.environ.get('NEKRYPTA_INSTALLER') == 'install.sh'
    hide = (lambda text: argparse.SUPPRESS) if wrapper else (lambda text: text)
    parser = argparse.ArgumentParser(prog='./install.sh' if wrapper else None, description=(
        'Install the packages NEKRYPTA needs on this machine, then NEKRYPTA, showing each step first.' if wrapper else __doc__))
    parser.add_argument('--home', type=Path, default=Path.home(), help='target home (also useful for isolated testing); '
                        'for one other than yours only files change, and your running session is left alone')
    parser.add_argument('--apply', action='store_true', help=hide('write the changes; without it, only show them'))
    parser.add_argument('--yes', action='store_true', help=('install packages, uninstall without asking, and ' if wrapper else '')
                        + 'replace every existing binding that clashes with a NEKRYPTA shortcut (except ones you chose to keep) without asking')
    parser.add_argument('--force', action='store_true', help='install even when required packages are missing')
    parser.add_argument('--shell-only', action='store_true', help='update the shell and the nekrypta command without touching shortcuts, Hyprland configuration or service units')
    parser.add_argument('--wallpaper-shortcut', action='store_true', help='also bind Super+Shift+W to the wallpaper picker')
    parser.add_argument('--ask-again', action='store_true', help='ask again about the clashing bindings you chose to keep before')
    parser.add_argument('--uninstall', action='store_true', help='remove NEKRYPTA and put back what it replaced; '
                        + ('shows what it does, then asks' if wrapper else 'with --apply, asks before it does'))
    parser.add_argument('--purge', action='store_true', help='with --uninstall: also delete settings and backups')
    parser.add_argument('--rollback', type=Path, metavar='BACKUP_DIRECTORY', help=hide('undo the --apply that made this backup'))
    parser.add_argument('--setup', action='store_true', help=hide('first install packages and services this machine needs (used by ./install.sh)'))
    parser.add_argument('--no-packages', action='store_true', help=('' if wrapper else 'with --setup: ') + 'skip packages and services')
    parser.add_argument('--no-login-screen', action='store_true', help=('' if wrapper else 'with --setup: ') + 'never add the SDDM login screen')
    parser.add_argument('--verbose', action='store_true', help='list every shell file')
    parser.add_argument('--version', action='version', version=f'NEKRYPTA {version()}')
    args = parser.parse_args()
    try:
        home = args.home.expanduser().resolve()
        if args.rollback:
            skipped = rollback(home, args.rollback.expanduser(), args.apply)
            if skipped:
                parser.exit(1, f'{skipped} file{"s" if skipped > 1 else ""} left alone (SKIP above); check {"them" if skipped > 1 else "it"} yourself.\n')
            return
        if args.uninstall:
            skipped = uninstall(home, args.apply, args.purge, args.yes, verbose=args.verbose)
            if skipped:
                parser.exit(1, f'{skipped} file{"s" if skipped > 1 else ""} left alone (skip above); check {"them" if skipped > 1 else "it"} yourself.\n')
            return
        print(BANNER)
        if args.purge:
            print('Note: --purge only applies with --uninstall; ignored.\n')
        if not args.setup and (args.no_packages or args.no_login_screen):
            print('Note: --no-packages and --no-login-screen only apply with --setup (./install.sh); ignored.\n')
        check_config_format(home, args.shell_only, args.wallpaper_shortcut)
        if args.setup and not args.no_packages:
            needs = setup_system(home, apply=args.apply, yes=args.yes, interactive=sys.stdin.isatty(), login_screen=not args.no_login_screen)
            if not args.apply and needs['config'] and not (home / '.config/hypr/hyprland.lua').exists():
                print("The shell's own changes depend on that hyprland.lua, which --apply creates first.")
                return
        session = own_home(home)
        # An installation under the previous name is moved over first (see scripts/legacy_migration.py).
        migrated = legacy_migration.migrate(home, args.apply, live=bool(session))
        running = args.apply and session and (migrated or shell_running())
        install(home, args.apply, args.shell_only, args.wallpaper_shortcut, yes=args.yes, force=args.force, verbose=args.verbose,
                ask_again=args.ask_again, hints=not running)
        if migrated and args.apply and not session:
            print()
            session_skipped(home)
        if running:
            sys.stdout.flush()
            subprocess.run([str(home / '.local/bin/nekrypta'), 'restart'])
            print('\nStarted the shell.' + (' If shortcuts changed: hyprctl reload' if os.environ.get('HYPRLAND_INSTANCE_SIGNATURE') else ''))
    except (ValueError, OSError, KeyError) as exc:
        parser.exit(1, f'Error: {exc}\n')
    except (KeyboardInterrupt, EOFError):
        parser.exit(130, '\nCancelled; nothing was written.\n')


if __name__ == '__main__':
    main()
