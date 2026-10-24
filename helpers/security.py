"""Security-center orchestration over the existing Mullvad and exposure adapters.

This module deliberately reports unknown coverage instead of inventing protection.
It never runs a shell and never kills a media process.
"""
import asyncio
import datetime
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import urllib.request

try:
    from mullvad import PREFERRED_RELAYS
except ImportError:  # imported as helpers.security
    from helpers.mullvad import PREFERRED_RELAYS

TAILSCALE_DNS = {"100.100.100.100", "fd7a:115c:a1e0::53"}
LOCAL_STUBS = {"127.0.0.53", "127.0.0.54"}
PUBLIC_DNS = {"9.9.9.9", "149.112.112.112", "1.1.1.1", "8.8.8.8"}
VIRTUAL_LINK = re.compile(r"^(docker\d*|br-[0-9a-f]+|veth[0-9a-f]+|virbr\d+|vnet\d+|lxcbr\d+|podman\d*|cni[-\w]*)$")


def parse_resolved_status(text):
    """Parse resolvectl's sectioned output without merging unrelated links."""
    sections, current, last_key = [], None, None
    for raw_line in str(text).splitlines():
        line = raw_line.rstrip()
        if line == "Global" or line.startswith("Link "):
            if current is not None:
                sections.append(current)
            current = {"name": "global" if line == "Global" else line.split("(", 1)[1].rstrip(")"),
                       "defaultRoute": None, "servers": [], "domains": []}
            last_key = None
            continue
        if current is None or not line.strip():
            continue
        match = re.match(r"^\s*([A-Za-z][A-Za-z ]*?):\s*(.*)$", line)
        if match and not (raw_line[:1].isspace() and last_key and raw_line.startswith(" " * 11) and ":" in match.group(1)):
            key, value = match.group(1).strip(), match.group(2).strip()
            last_key = key
        elif last_key and raw_line[:1].isspace():
            # A value wrapped onto the next line (resolvectl aligns it under the first one).
            key, value = last_key, line.strip()
        else:
            continue
        if key == "DNS Servers" or (key == "Current DNS Server" and current["name"] != "global"):
            # The global "Current DNS Server" is the built-in fallback in use, not a configured resolver.
            for server in value.split():
                server = server.split("#", 1)[0]
                if server and server not in current["servers"]:
                    current["servers"].append(server)
        elif key in {"DNS Domain", "DNS Domains"} and value:
            current["domains"].extend(value.split())
        elif key in {"DefaultRoute", "Default Route"}:
            current["defaultRoute"] = value.lower() == "yes"
        elif key == "Protocols" and current["defaultRoute"] is None:
            if "+DefaultRoute" in value:
                current["defaultRoute"] = True
            elif "-DefaultRoute" in value:
                current["defaultRoute"] = False
    if current is not None:
        sections.append(current)
    return sections


def _tailnet_domain(domain):
    return domain.endswith(".ts.net") or domain.startswith("~") and domain != "~." or "in-addr.arpa" in domain or "ip6.arpa" in domain


def _dns_scope(name, domains, default_route):
    if name.startswith("tailscale") and not default_route and domains and all(_tailnet_domain(domain) for domain in domains):
        return "tailnet"
    if default_route:
        return "público"
    return "local"


def _is_mullvad_link(name):
    return "mullvad" in name or name.startswith("wg")


# What a failure of the Mullvad controller is called to the user (the controller's own messages are English).
MULLVAD_MESSAGES = {
    "failed": "Uma verificação obrigatória não passou (relay aprovado, lockdown, LAN, DNS ou túnel).",
    "timeout": "A Mullvad não conectou a tempo.",
    "daemon_down": "O serviço da Mullvad não está em execução.",
    "not_logged_in": "A Mullvad está sem login.",
    "unsupported": "A versão da Mullvad não oferece algo que o modo exige.",
    "incompatible": "Versão da Mullvad não suportada.",
    "busy": "Outra operação de segurança está em andamento.",
}


class SecurityError(Exception):
    def __init__(self, code, message, retryable=False, details=None):
        super().__init__(message)
        self.code, self.message, self.retryable, self.details = code, message, retryable, details or {}


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _error(code, message, retryable=False, details=None):
    raise SecurityError(code, message, retryable, details)


def _base(state="unavailable", stale=False, capabilities=None, data=None, warnings=None):
    return {"state": state, "checkedAt": _now(), "stale": stale,
            "capabilities": capabilities or {}, "data": data or {}, "warnings": warnings or []}


def _mullvad_summary(status, external=None):
    raw = status.get("mullvad", {}) if isinstance(status, dict) else {}
    tunnel = {"connected": "conectado", "connecting": "conectando", "disconnected": "desconectado",
              "blocked": "bloqueado", "error": "erro"}.get(raw.get("tunnelState"), "unknown")
    relay = status.get("relay") if isinstance(status, dict) else None
    relay_data = None
    if isinstance(relay, dict):
        relay_data = {"direction": "saida", "country": relay.get("country") or None,
                      "city": relay.get("city") or None, "protocol": relay.get("protocol") or None,
                      "hostnameMasked": _mask_hostname(relay.get("hostname"))}
    incoming = status.get("incomingRelay") if isinstance(status, dict) else None
    incoming_data = None
    if isinstance(incoming, dict):
        incoming_data = {"direction": "entrada", "country": incoming.get("country") or None,
                         "city": incoming.get("city") or None, "protocol": incoming.get("protocol") or None,
                         "hostnameMasked": _mask_hostname(incoming.get("hostname"))}
    summary = {"daemon": raw.get("daemon"), "tunnel": tunnel, "isMullvad": None,
               "ipMasked": raw.get("exitIpMasked"), "relay": relay_data, "incomingRelay": incoming_data,
               "lockdown": status.get("lockdown") if isinstance(status, dict) else None,
               "lan": status.get("lan") if isinstance(status, dict) else None,
               "dns": status.get("dns") if isinstance(status, dict) else None,
               "split": bool(status.get("splitTunnel", {}).get("enabled")) if isinstance(status, dict) else None,
               "hardening": status.get("hardening") if isinstance(status, dict) else None}
    if isinstance(status, dict) and isinstance(status.get("endpoint"), dict):
        summary["hardening"] = {**(summary["hardening"] or {}),
                                 "obfuscation": status["endpoint"].get("obfuscation")}
    if isinstance(external, dict):
        summary["isMullvad"] = external.get("isMullvad")
        summary["ipMasked"] = external.get("ipMasked")
    return summary


def _mask_hostname(value):
    if not isinstance(value, str) or not value:
        return None
    parts = value.split("-")
    return "-".join(parts[:2] + ["xxx"]) if len(parts) > 2 else value


def classify_listener(item):
    scope = item.get("scope")
    if scope == "loopback":
        return "baixo", "A escuta está limitada ao próprio computador."
    if scope == "lan":
        return "atencao", "O serviço está acessível pela rede local."
    if scope == "all":
        return "atencao_elevada", "O serviço está escutando em todas as interfaces."
    return "nao_verificado", "Não foi possível verificar o escopo da escuta."


async def _exec_readonly(binary, *args, timeout=5):
    if shutil.which(binary) is None:
        _error("dependency_missing", f"Dependência ausente: {binary}.", False, {"binary": binary})
    process = await asyncio.create_subprocess_exec(binary, *args, stdin=asyncio.subprocess.DEVNULL,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout)
    except asyncio.TimeoutError:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        await asyncio.gather(process.wait(), return_exceptions=True)
        _error("timeout", f"{binary} não respondeu no prazo.", True, {"binary": binary})
    if process.returncode:
        _error("verification_failed", f"Não foi possível ler {binary}.", True, {"binary": binary})
    return stdout.decode(errors="replace")


async def pipewire_media_probe():
    """Read active PipeWire objects once; this is deliberately not a monitor."""
    raw = await _exec_readonly("pw-dump")
    try:
        objects = json.loads(raw)
    except (TypeError, ValueError):
        _error("verification_failed", "A resposta do PipeWire não pôde ser interpretada.", True)
    objects = objects if isinstance(objects, list) else []
    sources = {obj.get("id") for obj in objects if isinstance(obj, dict)
               and str(obj.get("info", {}).get("props", {}).get("media.class", "")) in {"Audio/Source", "Video/Source"}}
    streams = {}
    for obj in objects:
        props = obj.get("info", {}).get("props", {}) if isinstance(obj, dict) else {}
        media_class = str(props.get("media.class", ""))
        kind = "microphone" if media_class == "Stream/Input/Audio" else None
        if media_class == "Stream/Input/Video":
            kind = "camera"
        if kind is None:
            continue
        streams[obj.get("id")] = (obj, kind)

    # A source device alone is not capture. A source becomes relevant when a
    # client input stream exists, or an active PipeWire link connects them.
    active_stream_ids = set(streams)
    for obj in objects:
        if not isinstance(obj, dict) or obj.get("type") != "PipeWire:Interface:Link":
            continue
        props = obj.get("info", {}).get("props", {})
        state = str(props.get("link.state", props.get("state", ""))).lower()
        if state not in {"active", "running", "negotiating"}:
            continue
        output_id = props.get("link.output.node")
        input_id = props.get("link.input.node")
        if output_id in sources and input_id in streams:
            active_stream_ids.add(input_id)

    items = []
    for stream_id in active_stream_ids:
        obj, kind = streams[stream_id]
        props = obj.get("info", {}).get("props", {})
        pid = props.get("application.process.id")
        try:
            pid = int(pid) if pid is not None else None
        except (TypeError, ValueError):
            pid = None
        portal = str(props.get("pipewire.access", "")).lower() == "portal" or bool(props.get("portal"))
        items.append({"id": f"pipewire:{obj.get('id', len(items))}", "kind": kind,
                      "app": props.get("application.name"), "appId": props.get("application.id"),
                      "pid": pid, "startedAt": None, "origin": "portal" if portal else "pipewire",
                      "portal": portal})
    return items


class SecurityCenter:
    def __init__(self, mullvad, exposure_module, event_sink=None, external_fetch=None, media_probe=None,
                 permission_store=None, tailscale_policy=True, tailscale_peer=None):
        self.mullvad = mullvad
        self.exposure = exposure_module
        self.lock = asyncio.Lock()
        self.event_sink = event_sink
        self.external_fetch = external_fetch or self._fetch_external
        self.media_probe = media_probe or pipewire_media_probe
        self.permission_store = permission_store
        # Fixed by the project's personal configuration: Tailscale stays up, for Minecraft only.
        self.tailscale_policy = bool(tailscale_policy)
        self.tailscale_peer = tailscale_peer or None
        self._external_cache = None
        self._status_cache = None
        self._dns_cache = None
        self._tailscale_cache = None
        self._exception_cache = None
        if hasattr(mullvad, "exception_probe"):
            mullvad.exception_probe = self.exception_state

    def invalidate_cache(self):
        self._status_cache = None
        self._dns_cache = None
        self._external_cache = None
        self._tailscale_cache = None
        self._exception_cache = None

    async def _status(self):
        now = datetime.datetime.now(datetime.timezone.utc).timestamp()
        if self._status_cache and now - self._status_cache[0] < 2:
            return self._status_cache[1], False
        value = await self.mullvad.status()
        self._status_cache = (now, value)
        return value, False

    async def _tailscale(self):
        """What Tailscale is doing, read only: it is never stopped, restarted or reconfigured from here."""
        now = datetime.datetime.now(datetime.timezone.utc).timestamp()
        if self._tailscale_cache and now - self._tailscale_cache[0] < 30:
            return self._tailscale_cache[1]
        try:
            raw = await _exec_readonly("tailscale", "status", "--json", timeout=5)
            value = json.loads(raw)
            status = {"available": True,
                      "backendState": value.get("BackendState"),
                      "running": value.get("BackendState") == "Running",
                      "exitNode": bool((value.get("ExitNodeStatus") or {}).get("ID")),
                      "magicDns": bool(value.get("MagicDNSSuffix") or value.get("MagicDNS")),
                      "routeAll": None, "defaultRoute": None}
            try:
                prefs = json.loads(await _exec_readonly("tailscale", "debug", "prefs", timeout=5))
                status["routeAll"] = bool(prefs.get("RouteAll"))
                status["exitNode"] = status["exitNode"] or bool(prefs.get("ExitNodeID") or prefs.get("ExitNodeIP"))
            except (SecurityError, ValueError, TypeError, AttributeError):
                pass
            status["defaultRoute"] = await self._default_route_via_tailscale()
        except (SecurityError, ValueError, TypeError):
            status = {"available": False, "backendState": None, "running": None, "exitNode": None, "magicDns": None,
                      "routeAll": None, "defaultRoute": None}
        self._tailscale_cache = (now, status)
        return status

    @staticmethod
    async def _default_route_via_tailscale():
        """Whether a default route (any family) leaves through the Tailscale interface; None when unreadable."""
        found = False
        for family in ("-4", "-6"):
            try:
                routes = json.loads(await _exec_readonly("ip", "-j", family, "route", "show", "default", timeout=3))
            except (SecurityError, ValueError, TypeError):
                return None
            found = found or any(isinstance(route, dict) and str(route.get("dev", "")).startswith("tailscale")
                                 for route in routes if isinstance(routes, list))
        return found

    async def exception_state(self):
        """The Tailscale exception, proven or not: "inativo" (nothing to except), "permitido" (up, no exit node,
        no default route, MagicDNS confined to the tailnet), "nao_comprovado" or "bloqueado" (exit node)."""
        now = datetime.datetime.now(datetime.timezone.utc).timestamp()
        if self._exception_cache and now - self._exception_cache[0] < 20:
            return self._exception_cache[1]
        tailscale = await self._tailscale()
        if not tailscale.get("available") or not tailscale.get("running"):
            verdict = "inativo"
        elif tailscale.get("exitNode") is True or tailscale.get("routeAll") is True or tailscale.get("defaultRoute") is True:
            verdict = "bloqueado"
        elif tailscale.get("exitNode") is None or tailscale.get("defaultRoute") is None:
            verdict = "nao_comprovado"
        else:
            verdict = "permitido"
            try:
                sections = parse_resolved_status(await _exec_readonly("resolvectl", "status", timeout=5))
            except SecurityError:
                sections = None
            if sections is None:
                verdict = "nao_comprovado"
            else:
                link = next((item for item in sections if item["name"].startswith("tailscale")), None)
                if link is not None and _dns_scope(link["name"], link["domains"], link["defaultRoute"]) != "tailnet" and link["servers"]:
                    verdict = "nao_comprovado"
            if verdict == "permitido" and self.tailscale_peer and not await self._peer_reachable():
                verdict = "nao_comprovado"
        self._exception_cache = (now, verdict)
        return verdict

    async def _peer_reachable(self):
        try:
            await _exec_readonly("tailscale", "ping", "--c", "1", "--timeout", "4s", self.tailscale_peer, timeout=8)
            return True
        except SecurityError:
            return False

    def _emit(self, name, data):
        if self.event_sink:
            def redact(value):
                if isinstance(value, dict):
                    return {key: redact(item) for key, item in value.items()
                            if key not in {"organization", "country", "city"}}
                if isinstance(value, list):
                    return [redact(item) for item in value]
                return value
            self.event_sink(name, redact(data))

    async def _fetch_external(self):
        def fetch():
            request = urllib.request.Request("https://am.i.mullvad.net/json", headers={"Accept": "application/json"})
            with urllib.request.urlopen(request, timeout=5) as response:
                return json.loads(response.read(1024 * 1024))
        try:
            return await asyncio.to_thread(fetch)
        except Exception as exc:
            _error("timeout", "A verificação externa não respondeu.", True, {"url": "https://am.i.mullvad.net/json", "reason": type(exc).__name__})

    async def _external(self, manual=False):
        if not manual and self._external_cache and (datetime.datetime.now(datetime.timezone.utc).timestamp() - self._external_cache[0] < 300):
            return self._external_cache[1], False
        value = await self.external_fetch()
        self._external_cache = (datetime.datetime.now(datetime.timezone.utc).timestamp(), value)
        return value, False

    async def verify(self, manual=False):
        async with self.lock:
            try:
                status, _ = await self._status()
            except Exception as exc:
                _error(getattr(exc, "code", "verification_failed"), "Não foi possível verificar o estado da Mullvad.", True)
            data = {"local": _mullvad_summary(status),
                    "external": {"status": "indisponivel", "isMullvad": None, "ipMasked": None,
                                  "country": None, "city": None, "organization": None}}
            if hasattr(self.mullvad, "_read_hardening_locked"):
                try:
                    hardening = await self.mullvad._read_hardening_locked()
                    hardening["obfuscation"] = (status.get("endpoint") or {}).get("obfuscation")
                    data["local"]["hardening"] = hardening
                except Exception:
                    data["local"]["hardening"] = {"obfuscation": None, "quantumResistance": None, "daita": None}
            warnings = []
            data["tailscale"] = dict(await self._tailscale())
            data["tailscale"]["allowTailscaleMinecraft"] = self.tailscale_policy
            data["tailscale"]["exception"] = await self.exception_state()
            if data["tailscale"].get("exitNode") is True:
                warnings.append({"code": "tailscale_exception", "message": "Exit node do Tailscale não é permitido."})
            try:
                external, stale = await self._external(manual)
                external_ip = next((external.get(key) for key in ("ip", "ip_address", "ipv4")
                                    if isinstance(external.get(key), str)), None)
                external_is_mullvad = external.get("mullvad_exit_ip") is True or external.get("is_mullvad") is True
                external_data = {"isMullvad": external_is_mullvad,
                                 "ipMasked": self._mask_ip(external_ip) if isinstance(external_ip, str) else None,
                                 "country": external.get("country"), "city": external.get("city"),
                                 "organization": external.get("organization"),
                                 "status": "confirmado" if isinstance(external.get("mullvad_exit_ip"), bool) else "inconclusivo"}
                data["external"] = external_data
                data["local"] = _mullvad_summary(status, external_data)
                tunnel_connected = status.get("mullvad", {}).get("tunnelState") == "connected"
                if external_data["status"] == "confirmado" and tunnel_connected != external_is_mullvad:
                    external_data["status"] = "contraditorio"
                    warnings.append({"code": "verification_failed", "message": "A verificação externa contradiz o túnel local."})
            except SecurityError as exc:
                stale = True
                warnings.append({"code": exc.code, "message": exc.message, "details": exc.details})
            local = status.get("mullvad", {})
            tunnel_connected = local.get("tunnelState") == "connected"
            local_ok = (status.get("lockdown") is True and status.get("lan") == "block" and
                        status.get("dns") == "default" and status.get("tunnel") is True and
                        isinstance(status.get("relay"), dict) and
                        status.get("relay", {}).get("country_code") in PREFERRED_RELAYS)
            mode_state = getattr(self.mullvad, "_state", "desativado")
            mode_active = mode_state in {"ativando", "protegido", "protegido_com_excecao", "protecao_parcial",
                                         "falha_fechada", "desativando"}
            if status.get("mullvad", {}).get("daemon") is False:
                state = "unavailable"
            elif not tunnel_connected and status.get("lockdown") is False and not mode_active:
                state = "desativado"
            else:
                state = "partial" if tunnel_connected else ("failed" if mode_active else "unavailable")
            if status.get("lockdown") is True and not tunnel_connected:
                state = "fail_closed"
            elif tunnel_connected and not local_ok:
                state = "failed"
            elif tunnel_connected and not warnings and data["external"]["status"] == "confirmado":
                # verify() does not perform the local DNS audit.  A connected
                # tunnel therefore remains partial until overview() combines
                # it with an explicit no-leak DNS result.
                state = "partial"
            elif data["external"]["status"] == "contraditorio":
                state = "failed"
            if mode_active and data["tailscale"].get("running") and data["tailscale"]["exception"] == "bloqueado":
                state = "failed"
            if not mode_active and not tunnel_connected and data["external"]["status"] == "confirmado":
                warnings.append({"code": "security_inactive", "message": "Modo de Segurança desativado."})
            result = _base(state, stale, {"mullvad": True, "external": not warnings}, data, warnings)
            self._emit("security.changed", result)
            return result

    async def activate(self, country=None, confirm=False):
        if confirm is not True:
            _error("permission_denied", "Confirmação explícita é necessária.")
        async with self.lock:
            self.invalidate_cache()
            # An exit node would make Tailscale the default route: refused before anything is touched.
            tailscale = await self._tailscale()
            if tailscale.get("running") and (tailscale.get("exitNode") is True or tailscale.get("routeAll") is True):
                return {"accepted": False, "state": "desativado", "error": "tailscale_exception",
                        "message": "O Tailscale usa um Exit Node. Desligue o Exit Node (tailscale set --exit-node=) e tente de novo.",
                        "action": "Desativar o Exit Node"}
            try:
                self._emit("security.changed", _base("warning", False, {"mullvad": True},
                                                      {"step": "mullvad", "stepState": "iniciando"}, []))
                await self.mullvad.enable(country, confirm=True, finalize=False)
                self._emit("security.changed", _base("partial", False, {"mullvad": True},
                                                      {"step": "tunel_local", "stepState": "confirmado"}, []))
                external, _ = await self._external(True)
                external_ip = next((external.get(key) for key in ("ip", "ip_address", "ipv4")
                                    if isinstance(external.get(key), str)), None)
                external_is_mullvad = external.get("mullvad_exit_ip") is True or external.get("is_mullvad") is True
                self._emit("security.changed", _base("partial", False, {"external": True},
                                                      {"step": "ip_externo", "stepState": "confirmado" if external_is_mullvad else "falha",
                                                       "ipMasked": self._mask_ip(external_ip) if isinstance(external_ip, str) else None}, []))
                dns = await self.dns_audit(False)
                dns_ok = dns.get("data", {}).get("result") == "sem_vazamento"
                status = await self.mullvad.status()
                try:
                    hardening = await self.mullvad._read_hardening_locked()
                    hardening["obfuscation"] = (status.get("endpoint") or {}).get("obfuscation")
                except Exception:
                    hardening = {}
                local_ok = (status.get("lockdown") is True and status.get("lan") == "block" and
                            not status.get("splitTunnel", {}).get("items") and
                            hardening.get("obfuscation") is not None and
                            hardening.get("quantumResistance") is True and hardening.get("daita") is True)
                if not external_is_mullvad:
                    raise SecurityError("verification_failed", "A verificação externa não confirmou a Mullvad.", True)
                if dns.get("data", {}).get("result") == "vazamento":
                    raise SecurityError("verification_failed", "DNS fora do túnel Mullvad; corrija a configuração do systemd-resolved antes de tentar novamente.", False,
                                        {"orientation": "Remova DNS= no drop-in /etc/systemd/resolved.conf.d/ e aplique manualmente: sudo resolvectl revert <interface> && sudo systemctl restart systemd-resolved."})
                self._emit("security.changed", _base("partial", dns.get("stale", True), {"dns": True},
                                                      {"step": "dns", "stepState": "confirmado" if dns_ok else "inconclusivo"}, dns.get("warnings", [])))
                self._exception_cache = None
                exception = await self.exception_state()
                state = "protegido" if local_ok and dns_ok else "protecao_parcial"
                if state == "protegido":
                    state = {"permitido": "protegido_com_excecao", "inativo": "protegido"}.get(exception, "protecao_parcial")
                self.mullvad._transition(state, {"step": "protegido" if state != "protecao_parcial" else "protecao_parcial",
                                                  "stepState": "confirmado", "external": True, "dns": dns_ok,
                                                  "tailscale": exception})
                self.mullvad._start_watcher()
                return {"accepted": True, "state": state, "external": True, "dns": dns.get("data", {}).get("result"),
                        "tailscale": exception}
            except Exception as error:
                if not hasattr(error, "code") or not hasattr(error, "message"):
                    raise
                if isinstance(error, SecurityError):
                    self.mullvad._transition("falha_fechada", {"error": error.code, "message": error.message,
                                                                "retryable": error.retryable, "action": "Desativar e restaurar",
                                                                "lockdown": True})
                # A failure inside the Mullvad controller already left its own fail-closed or error state.
                state = getattr(self.mullvad, "_state", "falha_fechada")
                message = error.message if isinstance(error, SecurityError) else MULLVAD_MESSAGES.get(error.code, error.message)
                return {"accepted": False, "state": state, "error": error.code,
                        "message": message, "action": "Desativar e restaurar"}

    async def deactivate(self, confirm=False):
        if confirm is not True:
            _error("permission_denied", "Confirmação explícita é necessária.")
        async with self.lock:
            self.invalidate_cache()
            # Tailscale was never touched by the activation: nothing to bring back.
            return await self.mullvad.restore(True)

    @staticmethod
    def _mask_ip(value):
        try:
            ip = ipaddress.ip_address(str(value))
            if ip.version == 4:
                return ".".join(str(ip).split(".")[:2] + ["x", "x"])
            return ":".join(ip.exploded.split(":")[:2]) + ":x:x"
        except ValueError:
            return None

    async def overview(self):
        if self.lock.locked():
            # An activation or a restore is running: its own events carry the steps; this read does not queue behind it.
            return _base("warning", True, {"mullvad": True}, {"busy": True}, [])
        try:
            verified = await self.verify(False)
        except SecurityError as exc:
            return _base("unavailable", True, {"mullvad": False}, {}, [{"code": exc.code, "message": exc.message, "details": exc.details}])
        try:
            ports = await self.ports_list()
        except SecurityError as exc:
            ports = _base("unavailable", True, {}, {}, [{"code": exc.code, "message": exc.message, "details": exc.details}])
        try:
            lan = await self.lan_get()
        except Exception:
            lan = _base("unavailable", True, {}, {"state": "desconhecido", "value": None, "services": []}, [])
        try:
            split = await self.split_list()
        except Exception:
            split = _base("unavailable", True, {}, {"enabled": None, "count": None, "items": [], "vanished": [], "unassociated": []}, [])
        try:
            dns = await self.dns_audit(False)
        except Exception:
            dns = _base("unavailable", True, {}, {"local": {"servers": [], "interface": None, "route": None,
                                                       "customDns": None, "doh": None}, "result": "indisponivel",
                                               "external": {"available": False, "result": "nao_executado",
                                                             "checkedAt": None, "officialUrl": "https://mullvad.net/en/check"}}, [])
        try:
            media = await self.media_status()
        except SecurityError as exc:
            media = _base("unavailable", True, {"pipewire": False, "portal": False, "alsa_v4l2": "indisponivel"},
                           {"items": []}, [{"code": exc.code, "message": exc.message, "details": exc.details}])
        warnings = list(verified.get("warnings", []))
        warnings.extend(ports.get("warnings", []))
        warnings.extend(dns.get("warnings", []))
        warnings.extend(media.get("warnings", []))
        data = {**verified.get("data", {}), "ports": ports.get("data", {}),
                "lan": lan.get("data", {}), "split": split.get("data", {}),
                "dns": dns.get("data", {}), "media": media.get("data", {}), "alerts": warnings}
        if "local" in data:
            data["mullvad"] = data.pop("local")
        tunnel = data.get("mullvad", {}).get("tunnel")
        dns_result = data.get("dns", {}).get("result")
        if verified.get("state") == "fail_closed":
            state = "fail_closed"
        elif verified.get("state") == "failed":
            state = "failed"
        elif warnings:
            state = "warning"
        elif tunnel == "connected":
            # Protected is an affirmative claim.  It is only valid when the
            # same overview contains a confirmed no-leak DNS result.
            local = data.get("mullvad", {})
            hardening = local.get("hardening") or {}
            local_ready = (local.get("lockdown") is True and local.get("lan") == "block" and
                           local.get("dns") == "default" and not data.get("split", {}).get("count") and
                           hardening.get("obfuscation") is not None and
                           hardening.get("quantumResistance") is True and hardening.get("daita") is True)
            externally_confirmed = verified.get("data", {}).get("external", {}).get("status") == "confirmado"
            if local_ready and externally_confirmed and dns_result == "sem_vazamento":
                state = "protected"
                exception = data.get("tailscale", {}).get("exception")
                if exception == "bloqueado":
                    state = "failed"
                elif exception == "nao_comprovado":
                    state = "partial"
                elif exception == "permitido":
                    state = "protected_with_exception"
            else:
                state = "partial"
        else:
            state = verified.get("state", "unavailable")
        return _base(state, verified.get("stale", False) or ports.get("stale", False) or lan.get("stale", False) or split.get("stale", False),
                     {"mullvad": True, "ports": ports.get("state") != "unavailable", "lan": lan.get("state") != "unavailable",
                      "split": split.get("state") != "unavailable"}, data, warnings)

    async def ports_list(self):
        try:
            result = await self.exposure.audit()
        except Exception as exc:
            _error(getattr(exc, "code", "verification_failed"), "Não foi possível verificar os serviços em escuta.", True)
        items = []
        for item in result.get("listeners", []):
            classification, reason = classify_listener(item)
            process = item.get("process") or ""
            items.append({**item, "level": classification, "reason": reason,
                          "label": "Serviço em escuta", "service": process or None, "app": process or None,
                          "guidance": "Revise e desative o serviço pela configuração oficial; não o feche por aqui.",
                          "evidence": {"address": item.get("address"), "port": item.get("port"), "proto": item.get("proto")}})
        warnings = [{"code": "verification_failed", "message": "Não foi possível verificar um processo."}
                    for item in items if item.get("level") == "nao_verificado"]
        if any(item["level"] == "atencao_elevada" for item in items):
            state = "warning"
        else:
            state = "protected" if not warnings else "warning"
        return _base(state, False, {"ss": True}, {"items": items, "at": result.get("at")}, warnings)

    async def dns_audit(self, external=False):
        now = datetime.datetime.now(datetime.timezone.utc).timestamp()
        if not external and self._dns_cache and now - self._dns_cache[0] < 5:
            return self._dns_cache[1]
        try:
            resolved = await _exec_readonly("resolvectl", "status", timeout=5)
        except SecurityError as exc:
            result = _base("unavailable", True, {"local": False, "external": False},
                           {"result": "indisponivel", "lines": [], "external": {"result": "nao_executado",
                                                                                       "officialUrl": "https://mullvad.net/en/check"}},
                           [{"code": exc.code, "message": exc.message, "details": exc.details}])
            return result
        sections = parse_resolved_status(resolved)
        mode_active = getattr(self.mullvad, "_state", "desativado") not in ("desativado", "indisponivel")
        lines, public_outside, mullvad_dns = [], False, False
        # While the tunnel's resolver answers for every domain ("~."), no other link is asked for public names.
        catch_all = any(_is_mullvad_link(item["name"]) and item["servers"] and "~." in item["domains"] for item in sections)
        has_link_dns = any(item["servers"] for item in sections if item["name"] != "global")
        for section in sections:
            name = section["name"]
            scope = _dns_scope(name, section["domains"], section["defaultRoute"])
            for server in section["servers"]:
                if server in LOCAL_STUBS:
                    lines.append({"origem": "Sistema", "interface": "systemd-resolved", "finalidade": "resolvedor local",
                                  "escopo": "local", "estado": "informativo", "resumo": "Resolvedor local do sistema"})
                elif name.startswith("tailscale") and server in TAILSCALE_DNS:
                    restricted = scope == "tailnet"
                    lines.append({"origem": "Tailscale", "interface": name, "finalidade": "MagicDNS", "escopo": scope,
                                  "estado": "permitido" if restricted else "alerta",
                                  "resumo": "MagicDNS restrito à tailnet" if restricted else "MagicDNS com rota padrão"})
                    public_outside = public_outside or (mode_active and not restricted)
                elif _is_mullvad_link(name):
                    mullvad_dns = True
                    lines.append({"origem": "Mullvad", "interface": name, "finalidade": "DNS no túnel", "escopo": scope,
                                  "estado": "protegido", "resumo": "DNS público da Mullvad, dentro do túnel"})
                elif VIRTUAL_LINK.match(name):
                    lines.append({"origem": "Docker/VM", "interface": name, "finalidade": "rede virtual", "escopo": "local",
                                  "estado": "informativo", "resumo": "DNS de rede virtual, sem rota padrão"})
                elif name == "global":
                    leak = mode_active and not catch_all
                    lines.append({"origem": "Sistema", "interface": "global", "finalidade": "DNS global", "escopo": "global",
                                  "estado": "alerta" if leak else "informativo",
                                  "resumo": "DNS global configurado" + (" fora do túnel" if leak else "")})
                    public_outside = public_outside or leak
                else:
                    leak = mode_active and section["defaultRoute"] and not catch_all
                    lines.append({"origem": "Rede local", "interface": name, "finalidade": "DNS da interface", "escopo": scope,
                                  "estado": "alerta" if leak else "informativo",
                                  "resumo": "DNS da rede local" + (" fora do túnel" if leak else
                                                                   " (não usado: a Mullvad responde por todos os domínios)" if catch_all and section["defaultRoute"] else
                                                                   " sem rota padrão" if not section["defaultRoute"] else "")})
                    public_outside = public_outside or leak
        try:
            resolv_lines = Path("/etc/resolv.conf").read_text().splitlines()
            if any(line.strip() == "nameserver 127.0.0.53" for line in resolv_lines) and not any(
                    line["interface"] == "systemd-resolved" for line in lines):
                lines.insert(0, {"origem": "Sistema", "interface": "systemd-resolved", "finalidade": "resolvedor local",
                                 "escopo": "local", "estado": "informativo", "resumo": "Resolvedor local do sistema"})
        except OSError:
            pass
        if not mode_active:
            result_name = "nao_executado"
        elif public_outside:
            result_name = "vazamento"
        elif mullvad_dns:
            result_name = "sem_vazamento"
        else:
            result_name = "inconclusivo"
        tailscale = await self._tailscale()
        if tailscale.get("available") and tailscale.get("running") and (tailscale.get("exitNode") or tailscale.get("defaultRoute")):
            lines.append({"origem": "Tailscale", "interface": "tailscale0", "finalidade": "exit node",
                          "escopo": "público", "estado": "alerta", "resumo": "Exit node não permitido"})
            if mode_active:
                result_name = "vazamento"
        if external:
            lines.append({"origem": "Teste externo", "interface": None, "finalidade": "verificação", "escopo": "público",
                          "estado": "inconclusivo", "resumo": "Teste externo inconclusivo"})
        data = {"result": result_name, "lines": lines, "links": sections,
                "local": {"servers": [item["resumo"] for item in lines if item["interface"] != "systemd-resolved"],
                           "interface": next((item["interface"] for item in lines if item["origem"] == "Rede local"), None),
                           "route": next((item["escopo"] for item in lines if item["origem"] == "Rede local"), None),
                           "customDns": None, "doh": None},
                "external": {"available": False, "result": "indisponivel" if external else "nao_executado",
                             "checkedAt": None, "officialUrl": "https://mullvad.net/en/check"},
                "tailscale": tailscale}
        warnings = []
        if result_name == "vazamento":
            warnings.append({"code": "verification_failed", "message": "Resolvedor público fora do túnel Mullvad."})
        elif result_name == "inconclusivo":
            warnings.append({"code": "verification_failed", "message": "Não foi possível comprovar o caminho DNS."})
        result = _base("failed" if result_name == "vazamento" else "warning" if warnings else "protected",
                       False, {"local": True, "external": False, "tailscale": tailscale.get("available", False)}, data, warnings)
        if not external:
            self._dns_cache = (now, result)
        return result

    async def split_list(self):
        try:
            status, _ = await self._status()
        except Exception:
            return _base("unavailable", True, {"mullvad": False}, {}, [{"code": "verification_failed", "message": "Não foi possível verificar o split tunnel."}])
        split = status.get("splitTunnel", {})
        if split.get("enabled") is None:
            return _base("unavailable", True, {"mullvad": True}, {"enabled": None, "count": None, "items": [], "vanished": [], "unassociated": []},
                         [{"code": "verification_failed", "message": "Não foi possível verificar os processos excluídos."}])
        raw_items = split.get("items") if isinstance(split.get("items"), list) else []
        items = [{"pid": item.get("pid"), "app": item.get("app"), "exe": item.get("exe"),
                  "appId": item.get("appId"), "state": item.get("state", "ativo"), "detectedAt": item.get("detectedAt", _now())}
                 for item in raw_items if isinstance(item, dict) and isinstance(item.get("pid"), int)]
        return _base("warning" if items else "protected", False, {"mullvad": True},
                     {"enabled": bool(items), "count": len(items), "items": items, "vanished": [], "unassociated": []},
                     [{"code": "verification_failed", "message": "A CLI não forneceu os detalhes dos processos excluídos."}] if items and split.get("enabled") else [])

    async def lan_get(self):
        status = await self.mullvad.status()
        value = status.get("lan")
        state = {"block": "bloqueado", "allow": "permitido"}.get(value, "desconhecido")
        return _base("protected" if value else "unavailable", value is None, {"mullvad": True}, {"state": state, "value": value, "services": []},
                     [] if value else [{"code": "verification_failed", "message": "Não foi possível verificar a LAN."}])

    async def lan_set(self, blocked, confirm):
        if confirm is not True or type(blocked) is not bool:
            _error("permission_denied", "Confirmação explícita é necessária.")
        async with self.lock:
            async with self.mullvad.lock:
                await self.mullvad._exec("lan_block" if blocked else "lan_allow")
                status = await self.mullvad._status_locked()
            return await self.lan_get()

    async def media_status(self):
        try:
            items = await self.media_probe()
        except SecurityError:
            raise
        except Exception:
            _error("verification_failed", "Não foi possível verificar os fluxos de mídia.", True)
        return _base("warning" if items else "protected", False, {"pipewire": True, "portal": True, "alsa_v4l2": "limitado"}, {"items": items})

    async def media_revoke(self, flow_id, confirm):
        if confirm is not True:
            _error("permission_denied", "Confirmação explícita é necessária.")
        if self.permission_store is None:
            _error("unsupported", "Bloqueio individual não suportado.")
        try:
            result = await self.permission_store(flow_id)
        except Exception:
            _error("verification_failed", "Não foi possível revogar a permissão.", True)
        if isinstance(result, dict):
            revoked = result.get("revoked")
            verified = result.get("verified")
        else:
            revoked, verified = bool(result), None
        return _base("partial", False, {"portal": True}, {"id": flow_id, "revoked": revoked,
                                                            "futureOnly": True, "verified": verified}, [])

    async def close(self):
        return None
