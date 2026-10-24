"""Read-only Mullvad CLI adapter used by the security backend."""
import asyncio
import copy
import ipaddress
import json
import logging
import os
from pathlib import Path
import re
import shutil
import tempfile
import uuid
import datetime

import personal

LOG = logging.getLogger(__name__)
PREFERRED_RELAYS = tuple(personal.MULLVAD["relays"])
OBFUSCATION_MODE = personal.MULLVAD["obfuscation"]
VERSION_RE = re.compile(r"\b(\d+)\.(\d+)\b")
ALLOWED = {
    "version": ("version",), "account": ("account", "get"), "status": ("status", "-j"), "relays": ("relay", "list"),
    "lan": ("lan", "get"), "dns": ("dns", "get"), "split": ("split-tunnel", "list"), "auto": ("auto-connect", "get"),
    "lockdown": ("lockdown-mode", "get"), "anti": ("anti-censorship", "get"), "tunnel": ("tunnel", "get"),
    "lockdown_on": ("lockdown-mode", "set", "on"), "lockdown_off": ("lockdown-mode", "set", "off"),
    "lan_block": ("lan", "set", "block"), "lan_allow": ("lan", "set", "allow"),
    "dns_default": ("dns", "set", "default"), "dns_custom": ("dns", "set", "custom"),
    "anti_set": ("anti-censorship", "set", "mode"),
    "quantum_set": ("tunnel", "set", "quantum-resistant"), "daita_set": ("tunnel", "set", "daita"),
    "relay_get": ("relay", "get"), "relay_set": ("relay", "set", "location"), "relay_restore": ("relay", "set", "location"), "connect": ("connect",),
    "disconnect": ("disconnect",),
    "status_help": ("status", "--help"), "relay_help": ("relay", "--help"),
    "lan_help": ("lan", "--help"), "dns_help": ("dns", "--help"),
    "split_help": ("split-tunnel", "--help"), "account_help": ("account", "--help"),
    "split_delete": ("split-tunnel", "delete"),
    "split_add": ("split-tunnel", "add"),
    "anti_help": ("anti-censorship", "set", "mode", "--help"), "tunnel_help": ("tunnel", "set", "--help"),
}
UNKNOWN = "unknown"
SNAPSHOT_VERSION = 2
PROTECTED_STATES = ("protegido", "protegido_com_excecao", "protecao_parcial")
STATE_ALIASES = {"preparando": "ativando", "bloqueando": "ativando", "conectando": "ativando",
                 "verificando": "ativando", "restaurando": "desativando", "failed": "erro"}


class MullvadError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def _version(text):
    match = VERSION_RE.search(text)
    return match.group(0) if match else ""


def _json(text):
    try:
        return json.loads(text)
    except (TypeError, ValueError) as exc:
        raise MullvadError("invalid", "Mullvad returned invalid JSON.") from exc


def _relay_codes(value):
    found = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in {"country_code", "countrycode"} and isinstance(item, str) and item.lower() in PREFERRED_RELAYS:
                found.add(item.lower())
            found.update(_relay_codes(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_relay_codes(item))
    elif isinstance(value, str):
        for line in value.splitlines():
            match = re.fullmatch(r"\s*[^()\r\n]+\(([a-z]{2})\)\s*", line, re.I)
            if match and match.group(1).lower() in PREFERRED_RELAYS:
                found.add(match.group(1).lower())
    return found


def _relay_constraint(text):
    """The location constraint of `mullvad relay get` as `relay set location` takes it, a list of tokens:
    ["se"], ["se", "got"], ["se-got-wg-004"] or ["any"]. None when the output is not understood."""
    match = re.search(r"^\s*Location:\s*(.+?)\s*$", text or "", re.M | re.I)
    if not match:
        return None
    words = match.group(1).lower().replace(",", " ").split()
    if words in (["any"], ["any", "country"]):
        return ["any"]
    if words and words[0] in {"country", "city", "hostname"}:
        words = words[1:]
    return words if 1 <= len(words) <= 3 and all(re.fullmatch(r"[a-z0-9-]+", word) for word in words) else None


def _setting(text, allowed):
    value = text.strip().lower()
    for item in allowed:
        if re.search(rf"\b{re.escape(item)}\b", value):
            return item
    return None


def _relay_code(location):
    candidates = (location.get("country_code"), location.get("hostname", "").split("-", 1)[0])
    return next((str(value).lower() for value in candidates if isinstance(value, str) and value.lower() in PREFERRED_RELAYS), None)


def _dns_servers(text):
    return tuple(dict.fromkeys(re.findall(r"(?<![\w:])(?:\d{1,3}(?:\.\d{1,3}){3}|[0-9a-fA-F]{2,}(?::[0-9a-fA-F:]+)+)(?![\w:])", text)))


def _dns_setting(text):
    value = text.lower()
    if re.search(r"custom\s+dns\s*:\s*(?:no|false|off)\b", value):
        return "default"
    if re.search(r"custom\s+dns\s*:\s*(?:yes|true|on)\b", value):
        return "custom"
    return _setting(text, ("default", "custom"))


def _mask_ip(value):
    try:
        address = ipaddress.ip_address(str(value))
    except ValueError:
        return None
    if address.version == 4:
        return ".".join(str(address).split(".")[:3] + ["xxx"])
    return ":".join(str(address).split(":")[:4]) + ":xxxx"


def _relay_from_location(location, direction="saida"):
    if not isinstance(location, dict):
        return None
    hostname = location.get("hostname")
    country = location.get("country")
    city = location.get("city")
    if not any((hostname, country, city)):
        return None
    protocol = None
    if isinstance(hostname, str):
        if "-wg-" in hostname:
            protocol = "wireguard"
        elif "-obfs-" in hostname or "-tcp-" in hostname:
            protocol = "openvpn"
    return {"direction": direction, "hostname": hostname, "country": country or None,
            "city": city or None, "country_code": _relay_code(location), "protocol": protocol}


def _help_capabilities(text):
    """Extract command names from textual 2026.4 --help output, never JSON-assume."""
    return set(re.findall(r"(?m)^\s{2,}([a-z][a-z-]+)(?:\s|$)", text or ""))


def _split_entries(text):
    """Parse only excluded PID rows; the 'Excluded PIDs:' header is not an entry."""
    entries = []
    for line in (text or "").splitlines():
        match = re.fullmatch(r"\s*(\d+)\s*", line)
        if match:
            entries.append({"pid": int(match.group(1))})
    return entries


def _account_value(text):
    lowered = text.lower()
    if not text.strip():
        return None
    if "not logged" in lowered or "no account" in lowered:
        return False
    return True


def _relay_restriction(text):
    value = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if not value or not re.fullmatch(r"[A-Za-z0-9._:/,-]+", value):
        return None
    return value


def _anti_mode(text):
    match = re.search(r"\bmode\s*:\s*([a-z0-9-]+)", text or "", re.I)
    return match.group(1).lower() if match else _setting(text or "", ("auto", "off", "wireguard-port", "udp2tcp", "shadowsocks", "quic", "lwo"))


def _tunnel_options(text):
    """Parse tunnel get without retaining its WireGuard public key."""
    result = {"quantumResistance": None, "daita": None}
    for line in (text or "").splitlines():
        key, _, value = line.partition(":")
        value = value.strip().lower()
        if key.strip().lower() == "quantum resistance" and value in {"on", "off"}:
            result["quantumResistance"] = value == "on"
        elif key.strip().lower() == "daita" and value in {"true", "false", "on", "off"}:
            result["daita"] = value in {"true", "on"}
    return result


class Mullvad:
    def __init__(self, binary="mullvad", timeout=10, runner=None, event_sink=None, snapshot_path=None):
        self.binary, self.timeout, self._runner = binary, timeout, runner
        self.lock = asyncio.Lock()
        self._version = None
        self._state = "desativado"
        self._snapshot = None
        self._changed_keys = []
        self._dns_servers = ()
        self._selected_relay = None
        self._watch_task = None
        self._status_task = None
        # Set by the security center: "inativo", "permitido", "nao_comprovado" or "bloqueado" for the Tailscale exception.
        self.exception_probe = None
        self._event_sink = event_sink
        self._snapshot_path = Path(snapshot_path) if snapshot_path else None
        if self._snapshot_path:
            try:
                saved = json.loads(self._snapshot_path.read_text())
                if isinstance(saved, dict) and saved.get("session") is True:
                    if saved.get("version") in (1, SNAPSHOT_VERSION):
                        self._snapshot = saved
                        self._changed_keys = list(saved.get("changedKeys", []))
                        self._recovery_pending = True
            except (OSError, ValueError):
                pass
        if not hasattr(self, "_recovery_pending"):
            self._recovery_pending = False

    async def _exec(self, kind, timeout=None, *extra):
        args = ALLOWED[kind] + extra
        if kind == "split_delete" and (len(extra) != 1 or type(extra[0]) is not int or extra[0] <= 0):
            raise MullvadError("invalid", "Excluded split-tunnel PID is invalid.")
        if kind == "split_add" and (len(extra) != 1 or type(extra[0]) is not int or extra[0] <= 0):
            raise MullvadError("invalid", "Excluded split-tunnel PID is invalid.")
        if kind == "relay_set" and (len(extra) != 1 or extra[0] not in PREFERRED_RELAYS):
            raise MullvadError("invalid", "Choose an approved Mullvad relay.")
        if kind == "anti_set" and (len(extra) != 1 or extra[0] not in {"auto", "off", "wireguard-port", "udp2tcp", "shadowsocks", "quic", "lwo"}):
            raise MullvadError("invalid", "Choose a supported anti-censorship mode.")
        if kind in {"quantum_set", "daita_set"} and (len(extra) != 1 or extra[0] not in {"on", "off"}):
            raise MullvadError("invalid", "Choose on or off.")
        if kind == "relay_restore":
            saved = self._snapshot.get("relayRestriction") if self._snapshot else None
            if not saved or tuple(extra) != tuple(saved) or not all(re.fullmatch(r"[A-Za-z0-9-]+", str(token)) for token in extra):
                raise MullvadError("invalid", "Relay restore is limited to the captured restriction.")
        if kind == "dns_custom":
            if not extra or len(extra) > 8 or any(not isinstance(value, str) for value in extra):
                raise MullvadError("invalid", "DNS snapshot is invalid.")
            if self._snapshot is None or tuple(extra) != tuple(self._snapshot.get("dnsServers") or ()):
                raise MullvadError("invalid", "DNS restore is limited to the captured snapshot.")
            try:
                for value in extra:
                    ipaddress.ip_address(value)
            except ValueError:
                raise MullvadError("invalid", "DNS snapshot is invalid.") from None
        if self._runner is not None:
            result = await self._runner(*args)
            return result.decode(errors="replace") if isinstance(result, bytes) else str(result)
        if not shutil.which(self.binary):
            raise MullvadError("cli_missing", "Mullvad CLI is not installed.")
        LOG.debug("mullvad query: %s", kind)
        process = await asyncio.create_subprocess_exec(self.binary, *args, stdin=asyncio.subprocess.DEVNULL,
                                                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            stdout, _stderr = await asyncio.wait_for(process.communicate(), self.timeout if timeout is None else timeout)
        except asyncio.TimeoutError:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.wait(), 2)
            except asyncio.TimeoutError:
                pass
            raise MullvadError("timeout", "Mullvad did not finish in time.") from None
        if process.returncode:
            detail = _stderr.decode(errors="replace").lower() if isinstance(_stderr, bytes) else str(_stderr).lower()
            if "permission" in detail or "access denied" in detail:
                code = "permission_denied"
            elif "daemon" in detail or "service" in detail or "not running" in detail:
                code = "daemon_unavailable"
            elif "unknown command" in detail or "unrecognized" in detail or "invalid" in detail:
                code = "incompatible"
            elif "logged in" in detail or "account" in detail:
                code = "logged_out"
            else:
                code = "failed"
            raise MullvadError(code, "Mullvad query failed.")
        return stdout.decode(errors="replace")

    async def _check_version(self):
        if self._version is None:
            self._version = _version(await self._exec("version"))
        if self._version != "2026.4":
            raise MullvadError("version_unsupported", "Mullvad CLI 2026.4 is required.")

    async def version(self):
        async with self.lock:
            text = await self._exec("version")
            self._version = _version(text)
            return {"version": self._version, "supported": self._version == "2026.4"}

    async def capabilities(self):
        """Detect installed 2026.4 CLI syntax from version and textual help."""
        async with self.lock:
            await self._check_version()
            groups = ("status_help", "relay_help", "lan_help", "dns_help", "split_help", "account_help")
            result = {"version": self._version, "supported": self._version == "2026.4", "commands": {}}
            for group in groups:
                try:
                    text = await self._exec(group)
                except MullvadError:
                    result["commands"][group[:-5]] = None
                else:
                    result["commands"][group[:-5]] = sorted(_help_capabilities(text))
            return result

    async def _status_locked(self, emit_event=False):
            await self._check_version()
            try:
                data = _json(await self._exec("status"))
            except MullvadError as error:
                if error.code != "failed":
                    raise
                return {"state": "erro", "previousState": None, "relay": None, "tunnel": False,
                        "lockdown": None, "lan": None, "dns": None, "splitTunnel": {"enabled": None, "apps": None},
                        "unknown": ["status", "lockdown", "lan", "dns", "splitTunnel"],
                        "mullvad": {"cli": True, "daemon": False, "version": self._version, "loggedIn": None,
                                    "tunnelState": None}, "changedKeys": [], "incompleteRestore": [], "since": None,
                        "error": "daemon_down"}
            raw_state = str(data.get("state", "unknown")).lower()
            details = data.get("details") if isinstance(data.get("details"), dict) else {}
            location = details.get("location") if isinstance(details.get("location"), dict) else {}
            relay = _relay_from_location(location) if raw_state == "connected" else None
            incoming = None
            entry_hostname = location.get("entry_hostname")
            if raw_state == "connected" and entry_hostname:
                incoming = _relay_from_location({"hostname": entry_hostname}, "entrada")
            values, unknown = {}, []
            try:
                logged_in = _account_value(await self._exec("account"))
            except MullvadError:
                logged_in = None
            for key, allowed in (("lan", ("allow", "block")), ("dns", ("default", "custom"))):
                try:
                    raw = await self._exec(key)
                    values[key] = _dns_setting(raw) if key == "dns" else _setting(raw, allowed)
                    if key == "dns":
                        self._dns_servers = _dns_servers(raw)
                except MullvadError:
                    values[key] = None
                if values[key] is None:
                    unknown.append(key)
            try:
                lockdown_text = await self._exec("lockdown")
                lockdown = _setting(lockdown_text, ("on", "off"))
                json_lockdown = details.get("locked_down")
                values["lockdown"] = json_lockdown if isinstance(json_lockdown, bool) else (None if lockdown is None else lockdown == "on")
            except MullvadError:
                values["lockdown"] = None
            if values["lockdown"] is None:
                unknown.append("lockdown")
            try:
                split_text = await self._exec("split")
                entries = _split_entries(split_text)
                split = {"enabled": bool(entries), "apps": len(entries), "items": entries}
            except MullvadError:
                split = {"enabled": None, "apps": None, "items": None}
                unknown.append("splitTunnel")
            since = data.get("since")
            if logged_in is None:
                unknown.append("loggedIn")
            if since is None:
                unknown.append("since")
            result = {"state": self._state, "previousState": None, "relay": relay, "incomingRelay": incoming,
                    "tunnel": raw_state == "connected",
                    "lockdown": values["lockdown"], "lan": values["lan"], "dns": values["dns"], "splitTunnel": split,
                    "unknown": unknown, "mullvad": {"cli": True, "daemon": True, "version": self._version,
                    "loggedIn": logged_in, "tunnelState": raw_state,
                    "exitIpMasked": _mask_ip(location.get("mullvad_exit_ip"))},
                    "endpoint": {"obfuscation": (details.get("endpoint") or {}).get("obfuscation")}
                    if isinstance(details.get("endpoint"), dict) else {}, "changedKeys": [],
                    "incompleteRestore": [], "since": since}
            if emit_event and self._state != result["state"]:
                result["previousState"] = self._state
                self._state = result["state"]
                self._emit(result)
            return result

    async def _status_shared(self):
        async with self.lock:
            return await self._status_locked()

    async def status(self):
        """Concurrent readers share one CLI round: each caller gets its own copy of the same answer."""
        if self._recovery_pending:
            await self.recover()
        task = self._status_task
        if task is None or task.done():
            task = self._status_task = asyncio.ensure_future(self._status_shared())
        return copy.deepcopy(await asyncio.shield(task))

    async def _settled(self, expected):
        """The invariants' verdict, with the Tailscale exception said as such instead of as plain protection."""
        if expected != "protegido" or self.exception_probe is None:
            return expected
        try:
            verdict = await self.exception_probe()
        except Exception:
            verdict = "nao_comprovado"
        if verdict == "permitido":
            return "protegido_com_excecao"
        return "protecao_parcial" if verdict in {"nao_comprovado", "bloqueado"} else "protegido"

    async def recover(self):
        """Reconcile a persisted security session after a shell restart."""
        async with self.lock:
            if not self._recovery_pending:
                return self._state
            try:
                current = await self._status_locked()
                if current.get("mullvad", {}).get("daemon") is False or current.get("error") == "daemon_down":
                    self._transition("indisponivel", {"recovered": True, "message": "Não foi possível consultar o daemon da Mullvad.",
                                                        "error": "daemon_unavailable", "lockdown": current.get("lockdown")})
                    self._recovery_pending = False
                    return self._state
                tunnel_connected = current.get("tunnel") is True and current.get("mullvad", {}).get("tunnelState") == "connected"
                lockdown = current.get("lockdown")
                split_items = current.get("splitTunnel", {}).get("items") or []
                if lockdown is False and not tunnel_connected and current.get("lan") != "block" and not split_items:
                    self._clear_snapshot()
                    self._transition("desativado", {"recovered": True, "snapshotAbandoned": True,
                                                     "lockdown": False, "tunnel": False,
                                                     "message": "Sessão antiga abandonada; o Modo de Segurança está desativado."})
                    return self._state
                try:
                    current["hardening"] = await self._read_hardening_locked()
                    current["hardening"]["obfuscation"] = (current.get("endpoint") or {}).get("obfuscation")
                    state = await self._settled(self._verify_invariants(current))
                except MullvadError:
                    if lockdown is True and not tunnel_connected:
                        state = "falha_fechada"
                    elif lockdown is False and not tunnel_connected:
                        state = "failed"
                    elif lockdown is True and tunnel_connected:
                        state = "protecao_parcial"
                    else:
                        state = "erro"
                recovery_data = {"recovered": True, "lockdown": lockdown,
                                 "tunnel": tunnel_connected,
                                 "message": "Estado da sessão de segurança recuperado."}
                if state == "failed":
                    recovery_data.update({"message": "O Modo de Segurança deveria estar ativo, mas o túnel e o Lockdown estão desativados.",
                                          "action": "Desativar e restaurar", "retryable": False})
                self._transition(state, recovery_data)
                if state in PROTECTED_STATES:
                    self._start_watcher()
            except MullvadError as error:
                self._transition("indisponivel", {"recovered": True, "error": error.code,
                                                   "message": "Não foi possível consultar a sessão da Mullvad.",
                                                   "lockdown": "unknown"})
            except Exception as error:
                # Never leave the mode unread: say why it can't be told.
                self._transition("indisponivel", {"recovered": True, "error": "failed", "reason": type(error).__name__,
                                                   "message": "A recuperação da sessão falhou.", "lockdown": "unknown"})
            self._recovery_pending = False
            return self._state

    async def relays(self):
        async with self.lock:
            await self._check_version()
            text = await self._exec("relays")
            try:
                codes = _relay_codes(_json(text))
            except MullvadError:
                codes = _relay_codes(text)
            return {"preferred": [{"code": code, "name": code.upper(), "available": code in codes} for code in PREFERRED_RELAYS]}

    def _emit(self, data):
        if self._event_sink is not None:
            payload = dict(data)
            if isinstance(payload.get("relay"), dict):
                payload["relay"] = {key: value for key, value in payload["relay"].items()
                                     if key not in {"hostname", "country", "city"}}
            if isinstance(payload.get("incomingRelay"), dict):
                payload["incomingRelay"] = {key: value for key, value in payload["incomingRelay"].items()
                                             if key not in {"hostname", "country", "city"}}
            self._event_sink("security.state", payload)

    @staticmethod
    def _verify_invariants(status):
        relay = status.get("relay") or {}
        code = _relay_code(relay)
        hardening = status.get("hardening") or {}
        if (status.get("lockdown") is not True or status.get("lan") != "block" or status.get("dns") != "default"
                or status.get("tunnel") is not True or status.get("mullvad", {}).get("tunnelState") != "connected"
                or code not in PREFERRED_RELAYS or not isinstance(status.get("splitTunnel", {}).get("enabled"), bool)
                or hardening.get("obfuscation") is None or hardening.get("quantumResistance") is not True
                or hardening.get("daita") is not True):
            raise MullvadError("failed", "Security invariants are unknown or not satisfied.")
        return "protecao_parcial" if status["splitTunnel"]["enabled"] else "protegido"

    async def _cancel_watcher(self):
        task, self._watch_task = self._watch_task, None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def _transition(self, state, data=None):
        state = STATE_ALIASES.get(state, state)
        if self._state == state and not data:
            return
        payload = {"state": state, "previousState": self._state, "relay": None, "tunnel": False,
                   "lockdown": None, "lan": None, "dns": None, "splitTunnel": {"enabled": None, "apps": None},
                   "mullvad": {"cli": True, "daemon": None, "version": self._version, "loggedIn": None, "tunnelState": None},
                   "changedKeys": [], "incompleteRestore": [], "since": None, "unknown": []}
        payload.update(data or {})
        payload["previousState"] = self._state
        payload["state"] = state
        self._state = state
        self._emit(payload)

    def _persist_snapshot(self):
        if not self._snapshot_path or self._snapshot is None:
            return
        self._snapshot["updatedAt"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
        self._snapshot.setdefault("createdAt", self._snapshot["updatedAt"])
        self._snapshot.setdefault("sessionId", uuid.uuid4().hex)
        self._snapshot_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary_name = tempfile.mkstemp(prefix=f".{self._snapshot_path.name}.", dir=self._snapshot_path.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(self._snapshot, stream, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._snapshot_path)
            directory_fd = os.open(self._snapshot_path.parent, os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def _clear_snapshot(self):
        self._snapshot = None
        self._changed_keys = []
        self._recovery_pending = False
        if self._snapshot_path:
            try:
                tombstone = self._snapshot_path.with_name(f".{self._snapshot_path.name}.{uuid.uuid4().hex}.stale")
                os.replace(self._snapshot_path, tombstone)
                tombstone.unlink()
            except FileNotFoundError:
                pass

    async def _wait_connected_locked(self, timeout=45):
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            current = await self._status_locked()
            if current["mullvad"].get("tunnelState") == "connected":
                return current
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise MullvadError("timeout", "Mullvad did not connect in time.")
            await asyncio.sleep(min(0.5, remaining))

    async def enable(self, country=None, confirm=False, finalize=True, before_connect=None):
        async with self.lock:
            if confirm is not True:
                raise MullvadError("permission_denied", "Confirmação explícita é necessária.")
            if self._state not in ("desativado", "erro"):
                raise MullvadError("busy", "Security mode is already running.")
            if country is not None and (not isinstance(country, str) or country.lower() not in PREFERRED_RELAYS):
                raise MullvadError("invalid", "Choose an approved Mullvad country.")
            country = country.lower() if isinstance(country, str) else None
            self._transition("preparando")
            try:
                await self._check_version()
                before = await self._status_locked()
                if not before["mullvad"]["daemon"]:
                    raise MullvadError("daemon_down", "Mullvad daemon is not running.")
                if before["mullvad"]["loggedIn"] is not True:
                    raise MullvadError("not_logged_in", "Log in to Mullvad before enabling security mode.")
                now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
                self._snapshot = {"version": SNAPSHOT_VERSION, "session": True, "sessionId": uuid.uuid4().hex,
                                  "createdAt": now, "updatedAt": now, "changedKeys": []}
                try:
                    self._snapshot["antiCensorship"] = _anti_mode(await self._exec("anti"))
                    options = _tunnel_options(await self._exec("tunnel"))
                    self._snapshot["quantumResistance"] = options["quantumResistance"]
                    self._snapshot["daita"] = options["daita"]
                except MullvadError as error:
                    if error.code not in {"incompatible", "unsupported"}:
                        raise
                    self._snapshot["hardeningUnsupported"] = error.code
                try:
                    self._snapshot["relayRestriction"] = _relay_constraint(await self._exec("relay_get"))
                except MullvadError:
                    self._snapshot["relayRestriction"] = None
                self._snapshot["changedKeys"].extend(("antiCensorship", "quantumResistance", "daita"))
                if before.get("lockdown") is not True:
                    self._snapshot["lockdown"] = before.get("lockdown")
                    self._snapshot["changedKeys"].append("lockdown")
                if before.get("lan") != "block":
                    self._snapshot["lan"] = before.get("lan")
                    self._snapshot["changedKeys"].append("lan")
                split_items = before.get("splitTunnel", {}).get("items") or []
                if split_items:
                    self._snapshot["split"] = split_items
                    self._snapshot["changedKeys"].append("split")
                was_connected = before["mullvad"].get("tunnelState") == "connected"
                if not was_connected:
                    self._snapshot["wasConnected"] = False
                    self._snapshot["changedKeys"].append("tunnel")
                self._changed_keys = list(self._snapshot["changedKeys"])
                self._persist_snapshot()
            except MullvadError as error:
                self._transition("erro", {"state": "erro", "error": error.code})
                raise
            self._transition("bloqueando")
            lockdown_read = "unknown"
            try:
                await self._exec("lockdown_on")
                checked = await self._status_locked()
                lockdown_read = checked["lockdown"] if checked["lockdown"] is not None else "unknown"
                if checked["lockdown"] is not True:
                    raise MullvadError("failed", "Lockdown could not be verified; fail-closed state retained.")
                await self._exec("lan_block")
                checked = await self._status_locked()
                if checked["lan"] != "block":
                    raise MullvadError("failed", "LAN blocking could not be verified.")
                self._transition("bloqueando", {"step": "split", "stepState": "iniciando"})
                split_help = await self._exec("split_help")
                if split_help.strip() and not re.search(r"\bdelete\b", split_help, re.I):
                    raise MullvadError("unsupported", "A CLI 2026.4 não oferece remoção de exclusões split-tunnel.")
                for item in self._snapshot.get("split", []):
                    await self._exec("split_delete", None, item["pid"])
                    checked = await self._status_locked()
                    if item["pid"] in {entry.get("pid") for entry in checked.get("splitTunnel", {}).get("items", [])}:
                        raise MullvadError("failed", "A split-tunnel exclusion could not be removed.")
                hardening_warnings = []
                for kind, value, label in (("anti_set", OBFUSCATION_MODE, "antiCensorship"),
                                            ("quantum_set", "on", "quantumResistance"),
                                            ("daita_set", "on", "daita")):
                    try:
                        await self._exec(kind, None, value)
                    except MullvadError as error:
                        if error.code in {"incompatible", "unsupported"}:
                            hardening_warnings.append(label)
                        else:
                            raise
                await self._select_relay_locked(country)
                if before_connect is not None:
                    await before_connect(self._snapshot)
                self._transition("conectando")
                await self._exec("connect", 45)
                self._transition("verificando", {"step": "tunel_local", "stepState": "iniciando"})
                verified = await self._wait_connected_locked(45)
                verified["hardening"] = await self._read_hardening_locked()
                verified["hardening"]["obfuscation"] = (verified.get("endpoint") or {}).get("obfuscation")
                if (verified["hardening"].get("mode") not in {OBFUSCATION_MODE, "auto"}
                        or verified["hardening"].get("obfuscation") is None
                        or verified["hardening"].get("quantumResistance") is not True
                        or verified["hardening"].get("daita") is not True):
                    hardening_warnings.extend(key for key in ("antiCensorship", "quantumResistance", "daita")
                                              if key not in hardening_warnings)
                expected = "protecao_parcial" if hardening_warnings else self._verify_invariants(verified)
                self._transition("verificando", {"step": "tunel_local", "stepState": "confirmado"})
                if finalize:
                    self._transition(await self._settled(expected), {**verified, "hardeningWarnings": hardening_warnings})
                    self._start_watcher()
                return {"accepted": True}
            except MullvadError as error:
                self._transition("falha_fechada", {"state": "falha_fechada", "error": error.code,
                                                    "message": error.message, "retryable": error.code in {"timeout", "failed"},
                                                    "action": "Desativar e restaurar", "lockdown": lockdown_read})
                raise

    async def _select_relay_locked(self, country):
        """The approved list is what the mode connects through. The user's own constraint is captured in the snapshot
        and put back on restore; one already inside the list (or the country asked for) is kept as it is."""
        saved = self._snapshot.get("relayRestriction")
        current = (saved or ["any"])[0] if saved else None
        current_country = current.split("-", 1)[0] if current else None
        if country is None and current_country in PREFERRED_RELAYS:
            return
        if country is None:
            available = [item["code"] for item in (await self._relays_locked())["preferred"] if item["available"]]
            if not available:
                raise MullvadError("unsupported", "Nenhum relay aprovado está disponível.")
            country = available[0]
        self._transition("bloqueando", {"step": "relay", "stepState": "iniciando"})
        await self._exec("relay_set", None, country)
        if _relay_constraint(await self._exec("relay_get")) != [country]:
            raise MullvadError("failed", "The approved relay could not be verified.")
        self._snapshot["relaySelected"] = country
        if "relay" not in self._snapshot["changedKeys"]:
            self._snapshot["changedKeys"].append("relay")
        self._changed_keys = list(self._snapshot["changedKeys"])
        self._persist_snapshot()

    async def _relays_locked(self):
        await self._check_version()
        text = await self._exec("relays")
        try:
            codes = _relay_codes(_json(text))
        except MullvadError:
            codes = _relay_codes(text)
        return {"preferred": [{"code": code, "name": code.upper(), "available": code in codes} for code in PREFERRED_RELAYS]}

    async def _read_hardening_locked(self):
        try:
            anti = _anti_mode(await self._exec("anti"))
            options = _tunnel_options(await self._exec("tunnel"))
            return {"mode": anti, "obfuscation": None, "quantumResistance": options["quantumResistance"], "daita": options["daita"]}
        except MullvadError as error:
            if error.code in {"incompatible", "unsupported"}:
                return {"mode": None, "obfuscation": None, "quantumResistance": None, "daita": None, "unsupported": True}
            raise

    async def restore(self, confirm=True):
        async with self.lock:
            await self._cancel_watcher()
            if confirm is not True:
                raise MullvadError("invalid", "Explicit confirmation is required.")
            if self._snapshot is None:
                self._transition("falha_fechada", {"state": "falha_fechada", "error": "manual", "lockdown": "unknown"})
                return {"manual": True, "state": "falha_fechada", "lockdown": "unknown"}
            starting_state = self._state
            if starting_state not in ("falha_fechada", *PROTECTED_STATES):
                raise MullvadError("busy", "Security mode is not restorable in its current state.")
            self._transition("restaurando")
            incomplete, preserved, restored = [], [], []
            active_key = None
            was_fail_closed = starting_state == "falha_fechada" and self._snapshot.get("wasConnected") is False
            lockdown_read = "unknown"
            try:
                current = await self._status_locked()
                lockdown_read = current["lockdown"] if current["lockdown"] is not None else "unknown"
                if not current["mullvad"]["daemon"]:
                    raise MullvadError("daemon_down", "Mullvad daemon is not running.")
                for key in ("lan", "split", "tunnel", "relay", "antiCensorship", "quantumResistance", "daita"):
                    active_key = key
                    if key not in self._changed_keys:
                        continue
                    changed = key == "lan" and current["lan"] != "block"
                    if key == "split":
                        current_pids = {entry.get("pid") for entry in current.get("splitTunnel", {}).get("items", [])}
                        changed = False
                    if key == "tunnel":
                        changed = current["mullvad"].get("tunnelState") != "connected" and not was_fail_closed
                    if key in {"antiCensorship", "quantumResistance", "daita"}:
                        changed = False
                    if key == "relay":
                        changed = False
                    if changed:
                        preserved.append(key)
                        continue
                    if key == "lan" and self._snapshot.get("lan") in ("allow", "block") and self._snapshot["lan"] != "block":
                        await self._exec("lan_allow")
                        current = await self._status_locked()
                        if current["lan"] != self._snapshot["lan"]:
                            raise MullvadError("failed", "LAN restore could not be verified.")
                        restored.append(key)
                    elif key == "split":
                        for item in self._snapshot.get("split", []):
                            pid = item.get("pid")
                            if not isinstance(pid, int) or not os.path.exists(f"/proc/{pid}"):
                                preserved.append(f"split:{pid}")
                                continue
                            await self._exec("split_add", None, pid)
                            current = await self._status_locked()
                            current_pids = {entry.get("pid") for entry in current.get("splitTunnel", {}).get("items", [])}
                            if pid not in current_pids:
                                raise MullvadError("failed", "Split-tunnel restore could not be verified.")
                        restored.append(key)
                    elif key == "tunnel" and not self._snapshot.get("wasConnected") and current["mullvad"].get("tunnelState") == "connected":
                        await self._exec("disconnect")
                        current = await self._status_locked()
                        if current["mullvad"].get("tunnelState") == "connected":
                            raise MullvadError("failed", "Disconnect could not be verified.")
                        restored.append(key)
                    elif key == "relay":
                        saved = self._snapshot.get("relayRestriction")
                        selected = self._snapshot.get("relaySelected")
                        now = _relay_constraint(await self._exec("relay_get"))
                        if saved and selected and now == [selected]:
                            await self._exec("relay_restore", None, *saved)
                            if _relay_constraint(await self._exec("relay_get")) != saved:
                                raise MullvadError("failed", "Relay restore could not be verified.")
                            restored.append(key)
                        # Otherwise the user chose another relay meanwhile, or none was captured: it is left as it is.
                    elif key == "antiCensorship":
                        value = self._snapshot.get("antiCensorship") or "auto"
                        await self._exec("anti_set", None, value)
                        if _anti_mode(await self._exec("anti")) != value:
                            raise MullvadError("failed", "Anti-censorship restore could not be verified.")
                        restored.append(key)
                    elif key == "quantumResistance":
                        value = "on" if self._snapshot.get("quantumResistance") is True else "off"
                        await self._exec("quantum_set", None, value)
                        if _tunnel_options(await self._exec("tunnel"))["quantumResistance"] != self._snapshot.get("quantumResistance"):
                            raise MullvadError("failed", "Quantum resistance restore could not be verified.")
                        restored.append(key)
                    elif key == "daita":
                        value = "on" if self._snapshot.get("daita") is True else "off"
                        await self._exec("daita_set", None, value)
                        if _tunnel_options(await self._exec("tunnel"))["daita"] != self._snapshot.get("daita"):
                            raise MullvadError("failed", "DAITA restore could not be verified.")
                        restored.append(key)
                if incomplete:
                    raise MullvadError("failed", "Some settings could not be restored.")
                if "lockdown" in self._changed_keys:
                    active_key = "lockdown"
                    if current["lockdown"] is not True:
                        preserved.append("lockdown")
                    elif self._snapshot.get("lockdown") is False:
                        await self._exec("lockdown_off")
                        current = await self._status_locked()
                        lockdown_read = current["lockdown"] if current["lockdown"] is not None else "unknown"
                        if current["lockdown"] is not False:
                            raise MullvadError("failed", "Lockdown restore could not be verified.")
                        restored.append("lockdown")
                if preserved:
                    lockdown_read = current["lockdown"] if current["lockdown"] is not None else "unknown"
                    self._transition("erro", {"state": "erro", "incompleteRestore": incomplete, "preserved": preserved, "lockdown": lockdown_read})
                    return {"restored": restored, "preserved": preserved, "incompleteRestore": incomplete, "state": "erro", "lockdown": lockdown_read}
                self._clear_snapshot()
                self._transition("desativado", {"lockdown": lockdown_read})
                return {"restored": restored, "preserved": [], "incompleteRestore": [], "state": "desativado",
                        "lockdown": lockdown_read}
            except MullvadError as error:
                incomplete.append(active_key or error.code)
                try:
                    observed = await self._status_locked()
                    lockdown_read = observed["lockdown"] if observed["lockdown"] is not None else "unknown"
                except MullvadError:
                    lockdown_read = "unknown"
                self._transition("erro", {"state": "erro", "incompleteRestore": incomplete, "preserved": preserved, "error": error.code, "lockdown": lockdown_read})
                return {"restored": restored, "preserved": preserved, "incompleteRestore": incomplete, "state": "erro", "lockdown": lockdown_read}

    def _start_watcher(self):
        if self._watch_task is None or self._watch_task.done():
            self._watch_task = asyncio.create_task(self._watch_loop())

    async def _watch_loop(self):
        try:
            while self._state in PROTECTED_STATES:
                await asyncio.sleep(2)
                async with self.lock:
                    try:
                        current = await self._status_locked()
                    except MullvadError:
                        self._transition("falha_fechada", {"state": "falha_fechada", "error": "daemon_down", "lockdown": "unknown"})
                        return
                    try:
                        current["hardening"] = await self._read_hardening_locked()
                        current["hardening"]["obfuscation"] = (current.get("endpoint") or {}).get("obfuscation")
                        expected = self._verify_invariants(current)
                    except MullvadError as error:
                        lockdown = current["lockdown"] if current.get("lockdown") is not None else "unknown"
                        state = "falha_fechada" if lockdown is True and not current.get("tunnel") else "erro"
                        self._transition(state, {"state": state, "error": "tunnel_down", "message": error.message, "lockdown": lockdown})
                        return
                    expected = await self._settled(expected)
                    if expected != self._state:
                        self._transition(expected, current)
        except asyncio.CancelledError:
            return

    async def close(self):
        if self._watch_task is not None:
            self._watch_task.cancel()
            await asyncio.gather(self._watch_task, return_exceptions=True)
            self._watch_task = None
