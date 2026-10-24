import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from helpers import backend, security
from helpers.mullvad import Mullvad
from tests.backend.test_mullvad import EnableCli, RestoreCli

RESOLVED = """Global
  Protocols: +LLMNR +mDNS -DNSOverTLS DNSSEC=no/unsupported
  resolv.conf mode: stub
  Current DNS Server: 8.8.8.8#dns.google
Fallback DNS Servers: 9.9.9.9#dns.quad9.net

Link 2 (wlp1s0)
  Protocols: +DefaultRoute +LLMNR +mDNS
  DNS Servers: 192.168.15.1
  Default Route: yes

Link 3 (tailscale0)
  Protocols: -DefaultRoute -LLMNR -mDNS
  Current DNS Server: fd7a:115c:a1e0::53
  DNS Servers: 100.100.100.100 fd7a:115c:a1e0::53
  DNS Domain: tail52cdf5.ts.net ~0.e.1.a.c.5.1.1.a.7.d.f.ip6.arpa
              ~100.100.in-addr.arpa ~101.100.in-addr.arpa

Link 9 (wg0-mullvad)
  Protocols: +DefaultRoute
  DNS Servers: 10.64.0.1
  DNS Domain: ~.
"""
TAILSCALE = json.dumps({"BackendState": "Running", "MagicDNSSuffix": "tail52cdf5.ts.net"})
PREFS = json.dumps({"RouteAll": False, "ExitNodeID": "", "CorpDNS": True})


class Host:
    """Read-only commands of the host, recorded: a Tailscale command that changes anything fails the test."""
    def __init__(self, tailscale=TAILSCALE, prefs=PREFS, routes="[]"):
        self.calls, self.tailscale, self.prefs, self.routes = [], tailscale, prefs, routes

    async def __call__(self, binary, *args, timeout=5):
        self.calls.append((binary, *args))
        if binary == "tailscale" and args[:2] == ("status", "--json"):
            return self.tailscale
        if binary == "tailscale" and args[:2] == ("debug", "prefs"):
            return self.prefs
        if binary == "ip":
            return self.routes
        if binary == "resolvectl":
            return RESOLVED
        raise AssertionError(f"unexpected command {binary} {args}")

    def tailscale_mutations(self):
        return [call for call in self.calls if call[0] == "tailscale" and call[1] not in {"status", "debug", "ping"}]


def center_for(client, host, events=None):
    return security.SecurityCenter(client, object(), event_sink=(lambda name, data: events.append((name, data))) if events is not None else None,
                                   external_fetch=lambda: asyncio.sleep(0, result={"ip": "203.0.113.9", "mullvad_exit_ip": True}))


class ActivationFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_activation_keeps_tailscale_and_ends_protected_with_exception(self):
        host, events = Host(), []
        with tempfile.TemporaryDirectory() as directory, patch("helpers.security._exec_readonly", new=host):
            path = Path(directory) / "security-session.json"
            client = Mullvad(runner=RestoreCli(), snapshot_path=path, event_sink=lambda name, data: events.append((name, data)))
            center = center_for(client, host)
            result = await center.activate(None, True)
            self.assertEqual(result["state"], "protegido_com_excecao", result)
            self.assertEqual(result["tailscale"], "permitido")
            self.assertEqual(host.tailscale_mutations(), [])
            self.assertEqual(client._state, "protegido_com_excecao")
            # The persisted snapshot is private.
            self.assertEqual(oct(path.stat().st_mode & 0o777), "0o600")
            states = [data["state"] for name, data in events if name == "security.state"]
            self.assertEqual(states[-1], "protegido_com_excecao")
            self.assertLess(states.index("ativando"), len(states) - 1)
            deactivated = await center.deactivate(True)
            self.assertEqual(deactivated["state"], "desativado")
            self.assertEqual(host.tailscale_mutations(), [])
            self.assertFalse(path.exists())
            await client.close()

    async def test_exit_node_blocks_activation_without_touching_mullvad(self):
        host = Host(prefs=json.dumps({"RouteAll": True, "ExitNodeID": "n123"}))
        cli = EnableCli()
        with patch("helpers.security._exec_readonly", new=host):
            client = Mullvad(runner=cli)
            result = await center_for(client, host).activate(None, True)
        self.assertFalse(result["accepted"])
        self.assertEqual(result["error"], "tailscale_exception")
        self.assertEqual(client._state, "desativado")
        self.assertEqual(cli.calls, [])

    async def test_unproven_isolation_is_partial_never_protected(self):
        host = Host(routes=json.dumps([{"dst": "default", "dev": "tailscale0"}]))
        # A default route through Tailscale is an exit-node-like route: refused outright.
        with patch("helpers.security._exec_readonly", new=host):
            result = await center_for(Mullvad(runner=EnableCli()), host).exception_state()
        self.assertEqual(result, "bloqueado")
        unreadable = Host(routes="not json")
        with patch("helpers.security._exec_readonly", new=unreadable):
            result = await center_for(Mullvad(runner=EnableCli()), unreadable).exception_state()
        self.assertEqual(result, "nao_comprovado")

    async def test_a_controller_failure_is_answered_in_portuguese_and_never_as_success(self):
        host = Host()
        with patch("helpers.security._exec_readonly", new=host):
            client = Mullvad(runner=EnableCli(unverified=True))
            result = await center_for(client, host).activate(None, True)
        self.assertFalse(result["accepted"])
        self.assertEqual(result["state"], "falha_fechada")
        self.assertIn("verificação obrigatória", result["message"])
        self.assertEqual(host.tailscale_mutations(), [])

    async def test_inactive_tailscale_leaves_plain_protection(self):
        host = Host(tailscale=json.dumps({"BackendState": "Stopped"}))
        with patch("helpers.security._exec_readonly", new=host):
            client = Mullvad(runner=EnableCli())
            result = await center_for(client, host).activate(None, True)
        self.assertEqual(result["state"], "protegido")
        await client.close()

    async def test_dns_public_leak_fails_closed_and_can_be_restored(self):
        host = Host()
        leaky = RESOLVED.replace("DNS Domain: ~.", "DNS Domain: example.com")
        async def leaking(binary, *args, timeout=5):
            return leaky if binary == "resolvectl" else await host(binary, *args)
        with patch("helpers.security._exec_readonly", new=leaking):
            client = Mullvad(runner=EnableCli())
            result = await center_for(client, host).activate(None, True)
        self.assertEqual(result["state"], "falha_fechada")
        self.assertEqual(client._state, "falha_fechada")


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_restart_while_protected_recovers_the_exception_state(self):
        host = Host()
        with tempfile.TemporaryDirectory() as directory, patch("helpers.security._exec_readonly", new=host):
            path = Path(directory) / "security-session.json"
            cli = EnableCli()
            first = Mullvad(runner=cli, snapshot_path=path)
            await center_for(first, host).activate(None, True)
            await first.close()
            # The shell restarts: a new controller reads the snapshot and the live state.
            events = []
            second = Mullvad(runner=cli, snapshot_path=path, event_sink=lambda name, data: events.append(data))
            center_for(second, host)
            status = await second.status()
            self.assertEqual(status["state"], "protegido_com_excecao")
            self.assertTrue(any(event.get("recovered") for event in events))
            await second.close()

    async def test_stale_snapshot_never_overrides_the_real_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "security-session.json"
            path.write_text(json.dumps({"version": 2, "session": True, "changedKeys": ["lockdown", "tunnel"], "wasConnected": False}))
            client = Mullvad(runner=EnableCli(), snapshot_path=path)
            status = await client.status()
            self.assertEqual(status["state"], "desativado")
            self.assertFalse(path.exists())


class StableReadsTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_is_answered_as_unavailable_when_the_daemon_hangs(self):
        instance = backend.Backend()
        async def hang():
            await asyncio.sleep(60)
        with patch.object(instance.mullvad, "status", new=hang), patch.object(backend, "STATUS_DEADLINE", 0.05):
            result = await instance.dispatch("security.status", {})
        self.assertEqual(result["state"], "indisponivel")
        self.assertEqual(result["reason"], "timeout")
        self.assertTrue(result["message"])

    async def test_status_during_an_operation_is_answered_from_memory(self):
        instance = backend.Backend()
        instance.mullvad._state = "ativando"
        async with instance.mullvad.lock:
            started = time.monotonic()
            result = await instance.dispatch("security.status", {})
        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(result["state"], "ativando")
        self.assertTrue(result["inProgress"])

    async def test_normal_system_reads_as_disabled_with_a_timestamp(self):
        instance = backend.Backend()
        instance.mullvad._runner = EnableCli()
        result = await instance.dispatch("security.status", {})
        self.assertEqual(result["state"], "desativado")
        self.assertIs(result["lockdown"], False)
        self.assertEqual(result["mullvad"]["tunnelState"], "disconnected")
        self.assertFalse(result["stale"])

    async def test_concurrent_reads_share_one_cli_round(self):
        cli = EnableCli()
        client = Mullvad(runner=cli)
        await asyncio.gather(*(client.status() for _ in range(5)))
        self.assertEqual(cli.calls.count(("status", "-j")), 1)

    async def test_overview_during_activation_does_not_queue_behind_it(self):
        center = security.SecurityCenter(Mullvad(runner=EnableCli()), object())
        async with center.lock:
            result = await asyncio.wait_for(center.overview(), 1)
        self.assertTrue(result["data"]["busy"])

    async def test_security_events_carry_no_account_or_full_address(self):
        host, events = Host(), []
        with patch("helpers.security._exec_readonly", new=host):
            client = Mullvad(runner=EnableCli(), event_sink=lambda name, data: events.append((name, data)))
            center = security.SecurityCenter(client, object(), event_sink=lambda name, data: events.append((name, data)),
                                             external_fetch=lambda: asyncio.sleep(0, result={"ip": "203.0.113.9", "mullvad_exit_ip": True,
                                                                                              "country": "Albania", "city": "Tirana"}))
            await center.activate(None, True)
            await client.close()
        logged = json.dumps(events)
        self.assertNotIn("203.0.113.9", logged)
        self.assertNotIn("Albania", logged)
        self.assertNotIn("Tirana", logged)
        self.assertNotIn("account", logged.lower().replace("loggedin", ""))


if __name__ == "__main__":
    unittest.main()
