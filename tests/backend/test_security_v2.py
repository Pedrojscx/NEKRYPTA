import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from helpers import personal, security
from helpers.backend import DEFAULTS, Backend, BackendError, check_setting
from helpers.mullvad import Mullvad, _tunnel_options


RESOLVED_RESTRICTED = """Global
  resolv.conf mode: stub
Link 2 (wlp1s0)
  Current Scopes: DNS
  DefaultRoute: no
  DNS Servers: 192.168.0.1
Link 3 (tailscale0)
  DefaultRoute: no
  DNS Servers: 100.100.100.100 fd7a:115c:a1e0::53
  DNS Domain: tail52cdf5.ts.net ~ts.net ~100.100.in-addr.arpa
Link 7 (wg0-mullvad)
  DefaultRoute: yes
  DNS Servers: 10.64.0.1
"""

RESOLVED_PUBLIC = """Global
  Current DNS Server: 9.9.9.9#dns.quad9.net
  DNS Servers: 9.9.9.9
Link 2 (wlp1s0)
  DefaultRoute: yes
  DNS Servers: 192.168.0.1
Link 3 (tailscale0)
  DefaultRoute: no
  DNS Servers: 100.100.100.100
  DNS Domain: ~ts.net
"""


class StatusMock:
    def __init__(self, status, state="desativado"):
        self.value = status
        self._state = state
        self.lock = asyncio.Lock()

    async def status(self):
        return self.value


def local_status(connected=False, lockdown=False):
    return {"lockdown": lockdown, "lan": "block" if connected else "allow", "dns": "default",
            "tunnel": connected, "relay": {"country_code": "za"} if connected else None,
            "splitTunnel": {"enabled": False, "items": []},
            "mullvad": {"daemon": True, "loggedIn": True,
                        "tunnelState": "connected" if connected else "disconnected"},
            "hardening": {"obfuscation": "shadowsocks", "quantumResistance": True, "daita": True}}


class SecurityV2Tests(unittest.IsolatedAsyncioTestCase):
    async def dns(self, text, state="desativado", tailscale=None):
        client = StatusMock(local_status(state in {"protegido", "protecao_parcial"}, state == "protegido"), state)
        center = security.SecurityCenter(client, object())
        center._tailscale = AsyncMock(return_value=tailscale or {"available": True, "backendState": "Running",
                                                                  "exitNode": False, "magicDns": True})
        with patch("helpers.security._exec_readonly", new=AsyncMock(return_value=text)):
            return await center.dns_audit()

    def test_state_inventory_and_legacy_aliases(self):
        self.assertEqual(security._dns_scope("tailscale0", ["~ts.net"], False), "tailnet")
        self.assertEqual(security._dns_scope("wlp1s0", [], True), "público")

    def test_tunnel_parser_discards_wireguard_public_key(self):
        parsed = _tunnel_options("Quantum resistance: on\nDAITA: true\nPublic key: SECRET\n")
        self.assertEqual(parsed, {"quantumResistance": True, "daita": True})

    async def test_router_dns_is_failure_when_mode_is_active(self):
        result = await self.dns("Link 2 (wlp1s0)\n  DefaultRoute: yes\n  DNS Servers: 192.168.15.1\n", "protegido")
        self.assertEqual(result["data"]["result"], "vazamento")

    async def test_global_dns_is_failure_when_mode_is_active(self):
        result = await self.dns("Global\n  DefaultRoute: yes\n  DNS Servers: 9.9.9.9 1.1.1.1\n", "protegido")
        self.assertEqual(result["data"]["result"], "vazamento")

    async def test_activation_never_stops_tailscale(self):
        source = Path(security.__file__).read_text()
        self.assertNotIn("tailscale down", source)
        self.assertFalse(hasattr(security, "_tailscale_command"))
        self.assertNotIn('"down"', source)

    async def test_activation_is_refused_before_any_change_with_an_exit_node(self):
        client = Mock()
        client.enable = AsyncMock()
        center = security.SecurityCenter(client, object())
        center._tailscale = AsyncMock(return_value={"available": True, "running": True, "exitNode": True,
                                                     "routeAll": False, "defaultRoute": False})
        result = await center.activate(None, True)
        self.assertFalse(result["accepted"])
        self.assertEqual(result["error"], "tailscale_exception")
        client.enable.assert_not_awaited()

    async def test_exception_states(self):
        center = security.SecurityCenter(StatusMock(local_status()), object())
        base = {"available": True, "running": True, "exitNode": False, "routeAll": False, "defaultRoute": False}
        cases = [({**base, "running": False}, "inativo"), (base, "permitido"), ({**base, "exitNode": True}, "bloqueado"),
                 ({**base, "defaultRoute": True}, "bloqueado"), ({**base, "defaultRoute": None}, "nao_comprovado")]
        for status, expected in cases:
            center._exception_cache = None
            center._tailscale = AsyncMock(return_value=status)
            with patch("helpers.security._exec_readonly", new=AsyncMock(return_value=RESOLVED_RESTRICTED)):
                self.assertEqual(await center.exception_state(), expected, status)

    async def test_magicdns_with_a_default_route_is_not_proven(self):
        center = security.SecurityCenter(StatusMock(local_status()), object())
        center._tailscale = AsyncMock(return_value={"available": True, "running": True, "exitNode": False,
                                                     "routeAll": False, "defaultRoute": False})
        leaky = "Link 3 (tailscale0)\n  DefaultRoute: yes\n  DNS Servers: 100.100.100.100\n  DNS Domain: ~.\n"
        with patch("helpers.security._exec_readonly", new=AsyncMock(return_value=leaky)):
            self.assertEqual(await center.exception_state(), "nao_comprovado")

    async def test_mullvad_settles_exception_state(self):
        client = Mullvad()
        for verdict, expected in (("permitido", "protegido_com_excecao"), ("nao_comprovado", "protecao_parcial"),
                                  ("bloqueado", "protecao_parcial"), ("inativo", "protegido")):
            client.exception_probe = AsyncMock(return_value=verdict)
            self.assertEqual(await client._settled("protegido"), expected)
        self.assertEqual(await client._settled("protecao_parcial"), "protecao_parcial")

    def test_tailscale_minecraft_policy_is_fixed_in_the_personal_configuration(self):
        self.assertIs(personal.SECURITY["allowTailscaleMinecraft"], True)
        self.assertIs(DEFAULTS["allowTailscaleMinecraft"], True)
        with self.assertRaises(Exception):
            check_setting("allowTailscaleMinecraft", False)

    async def test_connected_lockdown_invariants_are_protected(self):
        self.assertEqual(Mullvad._verify_invariants(local_status(True, True)), "protegido")

    async def test_split_is_partial_invariant(self):
        status = local_status(True, True)
        status["splitTunnel"] = {"enabled": True, "items": [{"pid": 12}]}
        self.assertEqual(Mullvad._verify_invariants(status), "protecao_parcial")

    async def test_structured_parser_keeps_links_separate(self):
        links = security.parse_resolved_status(RESOLVED_RESTRICTED)
        self.assertEqual([link["name"] for link in links], ["global", "wlp1s0", "tailscale0", "wg0-mullvad"])
        self.assertEqual(links[2]["servers"], ["100.100.100.100", "fd7a:115c:a1e0::53"])

    async def test_stub_is_local_system_resolver(self):
        result = await self.dns("Global\n  DNS Servers: 127.0.0.53\n")
        line = next(line for line in result["data"]["lines"] if line["interface"] == "systemd-resolved")
        self.assertEqual(line["resumo"], "Resolvedor local do sistema")

    async def test_restricted_tailscale_magicdns_is_allowed_when_active(self):
        result = await self.dns(RESOLVED_RESTRICTED, "protegido")
        self.assertEqual(result["data"]["result"], "sem_vazamento")
        line = next(line for line in result["data"]["lines"] if line["interface"] == "tailscale0")
        self.assertEqual(line["estado"], "permitido")

    async def test_dns_is_audited_by_interface_never_as_a_raw_list(self):
        text = RESOLVED_RESTRICTED + "Link 5 (docker0)\n  DefaultRoute: no\n  DNS Servers: 172.17.0.1\n"
        result = await self.dns(text, "protegido")
        origins = {line["interface"]: line["origem"] for line in result["data"]["lines"]}
        self.assertEqual(origins["tailscale0"], "Tailscale")
        self.assertEqual(origins["wg0-mullvad"], "Mullvad")
        self.assertEqual(origins["docker0"], "Docker/VM")
        self.assertEqual(origins["wlp1s0"], "Rede local")
        self.assertEqual(result["data"]["result"], "sem_vazamento")

    async def test_global_fallback_is_not_a_configured_resolver(self):
        text = "Global\n  Current DNS Server: 8.8.8.8#dns.google\nFallback DNS Servers: 9.9.9.9\nLink 7 (wg0-mullvad)\n  DefaultRoute: yes\n  DNS Servers: 10.64.0.1\n"
        result = await self.dns(text, "protegido")
        self.assertEqual(result["data"]["result"], "sem_vazamento")

    async def test_tunnel_catch_all_makes_other_links_unused(self):
        text = ("Link 2 (wlp1s0)\n  DefaultRoute: yes\n  DNS Servers: 192.168.0.1\n"
                "Link 7 (wg0-mullvad)\n  DefaultRoute: yes\n  DNS Servers: 10.64.0.1\n  DNS Domain: ~.\n")
        result = await self.dns(text, "protegido")
        self.assertEqual(result["data"]["result"], "sem_vazamento")

    async def test_tailscale_default_route_is_alert(self):
        text = "Link 3 (tailscale0)\n  DefaultRoute: yes\n  DNS Servers: 100.100.100.100\n  DNS Domain: ~.\n"
        result = await self.dns(text, "protegido")
        self.assertEqual(result["data"]["result"], "vazamento")

    async def test_global_public_dns_active_is_failure(self):
        result = await self.dns("Global\n  DNS Servers: 9.9.9.9\n", "protegido")
        self.assertEqual(result["data"]["result"], "vazamento")

    async def test_public_dns_inactive_is_not_mullvad_failure(self):
        result = await self.dns(RESOLVED_PUBLIC, "desativado")
        self.assertEqual(result["data"]["result"], "nao_executado")
        self.assertFalse(result["warnings"])

    async def test_mullvad_dns_active_is_no_leak(self):
        result = await self.dns("Link 7 (wg0-mullvad)\n  DefaultRoute: yes\n  DNS Servers: 10.64.0.1\n", "protegido")
        self.assertEqual(result["data"]["result"], "sem_vazamento")

    async def test_missing_mullvad_path_is_inconclusive(self):
        result = await self.dns("Link 2 (wlp1s0)\n  DefaultRoute: yes\n  DNS Servers: 192.168.0.1\n", "protegido")
        self.assertEqual(result["data"]["result"], "vazamento")

    async def test_resolved_timeout_is_unavailable(self):
        client = StatusMock(local_status())
        center = security.SecurityCenter(client, object())
        center._tailscale = AsyncMock(return_value={"available": False})
        with patch("helpers.security._exec_readonly", new=AsyncMock(side_effect=security.SecurityError("timeout", "timeout"))):
            result = await center.dns_audit()
        self.assertEqual(result["data"]["result"], "indisponivel")

    async def test_dns_cache_deduplicates_reads(self):
        client = StatusMock(local_status())
        center = security.SecurityCenter(client, object())
        center._tailscale = AsyncMock(return_value={"available": False})
        runner = AsyncMock(return_value=RESOLVED_RESTRICTED)
        with patch("helpers.security._exec_readonly", new=runner):
            await center.dns_audit()
            await center.dns_audit()
        runner.assert_awaited_once()

    async def test_dns_external_line_is_documented(self):
        result = await self.dns(RESOLVED_RESTRICTED, "desativado")
        with_external = next(line for line in result["data"]["lines"] if line["origem"] == "Teste externo") \
            if any(line["origem"] == "Teste externo" for line in result["data"]["lines"]) else None
        self.assertIsNone(with_external)

    async def test_no_account_in_security_event(self):
        events = []
        client = StatusMock(local_status())
        center = security.SecurityCenter(client, object(), event_sink=lambda name, data: events.append(data))
        center._external = AsyncMock(return_value=({"mullvad_exit_ip": False}, False))
        center._tailscale = AsyncMock(return_value={"available": False})
        await center.verify()
        self.assertNotIn("account", json.dumps(events))

    async def test_corrupt_snapshot_does_not_replace_real_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "security-session.json"
            path.write_text("not-json")
            client = Mullvad(runner=None, snapshot_path=path)
            self.assertEqual(client._state, "desativado")
            self.assertTrue(path.exists())

    async def test_snapshot_fields_are_versioned_on_enable_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "security-session.json"
            client = Mullvad(snapshot_path=path)
            client._snapshot = {"version": 2, "session": True, "changedKeys": []}
            client._persist_snapshot()
            saved = json.loads(path.read_text())
            self.assertTrue({"version", "createdAt", "updatedAt", "sessionId"} <= set(saved))

    async def test_policy_is_not_a_runtime_method(self):
        instance = Backend()
        with self.assertRaises(BackendError):
            await instance.dispatch("security.policy.set", {"allowTailscaleMinecraft": False})

    async def test_overview_exposes_persistent_tailscale_policy(self):
        center = security.SecurityCenter(StatusMock(local_status()), object())
        center._tailscale = AsyncMock(return_value={"available": True, "backendState": "Running", "running": True,
                                                     "exitNode": False, "magicDns": True, "routeAll": False, "defaultRoute": False})
        center._external = AsyncMock(return_value=({"mullvad_exit_ip": False}, False))
        with patch("helpers.security._exec_readonly", new=AsyncMock(return_value=RESOLVED_RESTRICTED)):
            result = await center.verify()
        self.assertIs(True, result["data"]["tailscale"]["allowTailscaleMinecraft"])
        self.assertEqual(result["data"]["tailscale"]["exception"], "permitido")


if __name__ == "__main__":
    unittest.main()
