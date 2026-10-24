import asyncio
import os
import tempfile
import unittest

from helpers.mullvad import ALLOWED, Mullvad, MullvadError, PREFERRED_RELAYS


class FakeCli:
    def __init__(self, outputs):
        self.outputs, self.calls = outputs, []

    async def __call__(self, *args):
        self.calls.append(args)
        return self.outputs[args]


class EnableCli:
    def __init__(self, fail=None, unverified=False, bad=None):
        self.fail, self.calls, self.lockdown, self.lan, self.connected = fail, [], False, "allow", False
        self.unverified, self.bad = unverified, bad
        self.anti, self.quantum, self.daita = "auto", False, False
        # The location constraint, as `relay set location` takes it; "any" until something selects one.
        self.relay = ["any"]

    def code(self):
        return "al" if self.relay == ["any"] else self.relay[0].split("-")[0]

    def constraint(self):
        value = "any" if self.relay == ["any"] else ("country " if len(self.relay) == 1 and "-" not in self.relay[0] else "city ") + " ".join(self.relay)
        return f"Generic constraints\n    Location:               {value}\n    Provider(s):            any\n"

    async def __call__(self, *args):
        self.calls.append(args)
        if args == self.fail:
            raise MullvadError("failed", "mock failure")
        if args == ("lockdown-mode", "set", "on"):
            self.lockdown = True
        elif args == ("lan", "set", "block"):
            self.lan = "block"
        elif args == ("connect",):
            self.connected = True
        elif args[:3] == ("relay", "set", "location"):
            self.relay = list(args[3:])
        elif args[:3] == ("anti-censorship", "set", "mode"):
            self.anti = args[3]
        elif args[:3] == ("tunnel", "set", "quantum-resistant"):
            self.quantum = args[3] == "on"
        elif args[:3] == ("tunnel", "set", "daita"):
            self.daita = args[3] == "on"
        if args == ("version",):
            return "Mullvad VPN client 2026.4"
        if args == ("account", "get"):
            return "logged in fixture"
        if args == ("auto-connect", "get"):
            return "off"
        if args == ("anti-censorship", "get"):
            return f"mode: {self.anti}"
        if args == ("tunnel", "get"):
            return f"Quantum resistance: {'on' if self.quantum else 'off'}\nDAITA: {'true' if self.daita else 'false'}\nPublic key: SECRET-FIXTURE\n"
        if args == ("status", "-j"):
            code = "" if self.unverified else self.code()
            state = "connected" if self.connected else "disconnected"
            hostname = "xx-tia-wg-001" if self.unverified else f"{self.code()}-tia-wg-001"
            endpoint = '{"obfuscation":"%s"}' % self.anti if self.connected else '{}'
            return '{"state":"%s","account":{"logged_in":true},"details":{"locked_down":%s,"endpoint":%s,"location":{"hostname":"%s","country":"Albania","country_code":"%s","city":"Tirana"}}}' % (state, str(self.lockdown).lower(), endpoint, hostname, code)
        if args == ("lan", "get"):
            return "allow" if self.bad == "lan" else self.lan
        if args == ("dns", "get"):
            return "custom" if self.bad == "dns" else "default"
        if args == ("split-tunnel", "list"):
            return "No applications are excluded from the tunnel."
        if args == ("lockdown-mode", "get"):
            return "mystery" if self.bad == "lockdown" else ("on" if self.lockdown else "off")
        if args == ("relay", "list"):
            return "Albania (al)"
        if args == ("relay", "get"):
            return self.constraint()
        return ""


class RestoreCli(EnableCli):
    def __init__(self, initial_connected=False, readback_fail=None):
        super().__init__()
        self.connected = initial_connected
        # A connection that already existed runs through the user's own relay.
        self.relay = ["se"] if initial_connected else ["any"]
        self.dns = "default"
        self.readback_fail = readback_fail
        self.restore_phase = False

    async def __call__(self, *args):
        self.calls.append(args)
        if args == self.fail:
            raise MullvadError("failed", "mock failure")
        if args == ("lan", "set", "allow"):
            if not self.restore_phase or self.readback_fail != "lan":
                self.lan = "allow"
        elif args == ("dns", "set", "custom", "1.1.1.1"):
            if not self.restore_phase or self.readback_fail != "dns":
                self.dns = "custom 1.1.1.1"
        elif args == ("dns", "set", "default"):
            self.dns = "default"
        elif args == ("disconnect",):
            if not self.restore_phase or self.readback_fail != "tunnel":
                self.connected = False
        elif args == ("lockdown-mode", "set", "off"):
            if not self.restore_phase or self.readback_fail != "lockdown":
                self.lockdown = False
        elif args[:3] == ("anti-censorship", "set", "mode"):
            self.anti = args[3]
        elif args[:3] == ("tunnel", "set", "quantum-resistant"):
            self.quantum = args[3] == "on"
        elif args[:3] == ("tunnel", "set", "daita"):
            self.daita = args[3] == "on"
        if args == ("dns", "get"):
            return self.dns
        if args == ("anti-censorship", "get"):
            return f"mode: {self.anti}"
        if args == ("tunnel", "get"):
            return f"Quantum resistance: {'on' if self.quantum else 'off'}\nDAITA: {'true' if self.daita else 'false'}\nPublic key: SECRET-FIXTURE\n"
        if args == ("status", "-j"):
            hostname = self.code() + "-tia-wg-001" if self.connected else ""
            endpoint = '{"obfuscation":"%s"}' % self.anti if self.connected else '{}'
            return '{"state":"%s","account":{"logged_in":true},"details":{"locked_down":%s,"endpoint":%s,"location":{"hostname":"%s","country":"Albania","country_code":"%s","city":"Tirana"}}}' % ("connected" if self.connected else "disconnected", str(self.lockdown).lower(), endpoint, hostname, self.code() if self.connected else "")
        return await super().__call__(*args)


class MullvadTests(unittest.IsolatedAsyncioTestCase):
    async def test_enable_requires_explicit_confirmation(self):
        fake = EnableCli()
        with self.assertRaises(MullvadError) as raised:
            await Mullvad(runner=fake).enable()
        self.assertEqual(raised.exception.code, "permission_denied")
        self.assertEqual(fake.calls, [])

    async def test_session_snapshot_survives_controller_restart_as_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = f"{directory}/security-session.json"
            first = Mullvad(runner=EnableCli(), snapshot_path=path)
            first._snapshot = {"version": 1, "session": True, "changedKeys": ["lockdown"], "lockdown": False}
            first._changed_keys = ["lockdown"]
            first._persist_snapshot()
            second = Mullvad(runner=EnableCli(), snapshot_path=path)
            self.assertEqual(second._state, "desativado")
            self.assertEqual(second._snapshot["changedKeys"], ["lockdown"])

    async def test_restart_recovers_protected_state_from_persisted_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = f"{directory}/security-session.json"
            with open(path, "w") as stream:
                stream.write('{"version":1,"session":true,"changedKeys":["lockdown","lan","tunnel"],"wasConnected":false}')
            fake = EnableCli()
            fake.lockdown, fake.lan, fake.connected = True, "block", True
            fake.anti, fake.quantum, fake.daita = "shadowsocks", True, True
            events = []
            client = Mullvad(runner=fake, snapshot_path=path, event_sink=lambda event, data: events.append(data))
            result = await client.status()
            self.assertEqual(client._state, "protegido")
            self.assertEqual(result["state"], "protegido")
            self.assertEqual(result["mullvad"]["tunnelState"], "connected")
            self.assertTrue(any(event.get("state") == "protegido" and event.get("recovered") for event in events))
            await client.close()

    async def test_restart_with_snapshot_and_lockdown_without_tunnel_is_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = f"{directory}/security-session.json"
            with open(path, "w") as stream:
                stream.write('{"version":1,"session":true,"changedKeys":["lockdown","tunnel"],"wasConnected":false}')
            fake = EnableCli()
            fake.lockdown, fake.lan, fake.connected = True, "block", False
            client = Mullvad(runner=fake, snapshot_path=path)
            await client.status()
            self.assertEqual(client._state, "falha_fechada")
            await client.close()

    async def test_restart_with_snapshot_and_lockdown_off_is_failed_not_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = f"{directory}/security-session.json"
            with open(path, "w") as stream:
                stream.write('{"version":1,"session":true,"changedKeys":["lockdown","tunnel"],"wasConnected":false}')
            fake = EnableCli()
            fake.lockdown, fake.lan, fake.connected = False, "allow", False
            events = []
            client = Mullvad(runner=fake, snapshot_path=path, event_sink=lambda event, data: events.append(data))
            result = await client.status()
            self.assertEqual(client._state, "desativado")
            self.assertEqual(result["state"], "desativado")
            self.assertFalse(result["lockdown"])
            self.assertEqual(result["mullvad"]["tunnelState"], "disconnected")
            self.assertFalse(os.path.exists(path))
            self.assertTrue(any(event.get("state") == "desativado" and event.get("snapshotAbandoned")
                                for event in events))
            await client.close()

    async def test_split_exclusions_are_deleted_before_connect(self):
        class SplitCli(EnableCli):
            def __init__(self):
                super().__init__()
                self.excluded = {42}

            async def __call__(self, *args):
                if args == ("split-tunnel", "delete", 42):
                    self.calls.append(args)
                    self.excluded.remove(42)
                if args == ("split-tunnel", "list"):
                    self.calls.append(args)
                    return "Excluded PIDs:\n" + "\n".join(str(pid) for pid in sorted(self.excluded))
                return await super().__call__(*args)

        fake = SplitCli()
        client = Mullvad(runner=fake)
        await client.enable(confirm=True)
        self.assertIn(("split-tunnel", "delete", 42), fake.calls)
        self.assertLess(fake.calls.index(("split-tunnel", "delete", 42)), fake.calls.index(("connect",)))
        await client.close()

    def test_status_json_is_normalized_without_mutation(self):
        fake = FakeCli({
            ("version",): "Mullvad VPN client 2026.4\n",
            ("account", "get"): "logged in fixture",
            ("status", "-j"): '{"state":"connected","since":"fixture","account":{"logged_in":true},"details":{"locked_down":true,"location":{"hostname":"se-sto-wg-001","country":"Sweden","city":"Stockholm"}}}',
            ("lan", "get"): "allow\n",
            ("dns", "get"): "default\n",
            ("split-tunnel", "list"): "No applications are excluded from the tunnel.\n",
            ("lockdown-mode", "get"): "on\n",
        })
        result = asyncio.run(Mullvad(runner=fake).status())
        self.assertEqual(result["state"], "desativado")
        self.assertTrue(result["tunnel"])
        self.assertTrue(result["lockdown"])
        self.assertEqual(result["mullvad"]["tunnelState"], "connected")
        self.assertTrue(result["mullvad"]["loggedIn"])
        self.assertNotIn("account", result["mullvad"])
        self.assertEqual(result["unknown"], [])
        self.assertEqual(result["relay"]["hostname"], "se-sto-wg-001")
        self.assertNotIn("set", [arg for call in fake.calls for arg in call])

    def test_host_status_uses_json_lockdown_and_masks_location_ip(self):
        fake = FakeCli({
            ("version",): "Mullvad VPN client 2026.4",
            ("account", "get"): "logged in fixture",
            ("status", "-j"): '{"state":"connected","since":"fixture","details":{"locked_down":true,"location":{"mullvad_exit_ip":"198.51.100.25","hostname":"se-sto-wg-001","entry_hostname":"fi-hel-wg-001","country":"Sweden","city":"Stockholm"}}}',
            ("lan", "get"): "Local network sharing setting: block",
            ("dns", "get"): "Custom DNS: no\nBlock ads: false",
            ("split-tunnel", "list"): "Excluded PIDs:\n",
            ("lockdown-mode", "get"): "Block traffic when the VPN is disconnected: off",
        })
        result = asyncio.run(Mullvad(runner=fake).status())
        self.assertTrue(result["lockdown"])
        self.assertEqual(result["dns"], "default")
        self.assertEqual(result["mullvad"]["exitIpMasked"], "198.51.100.xxx")
        self.assertEqual(result["incomingRelay"]["hostname"], "fi-hel-wg-001")
        self.assertNotIn("latitude", result)

    async def test_relays_cover_d31_and_parse_fixture(self):
        fake = FakeCli({
            ("version",): "Mullvad VPN client 2026.4",
            ("relay", "list"): """Albania (al)\nJapan (jp)\nThailand (th)\nse-sto-wg-001\nStockholm se-sto\n""",
        })
        result = await Mullvad(runner=fake).relays()
        self.assertEqual([item["code"] for item in result["preferred"]], list(PREFERRED_RELAYS))
        available = {item["code"] for item in result["preferred"] if item["available"]}
        self.assertEqual(available, {"al", "jp", "th"})

    async def test_daemon_failure_is_not_reported_as_safe_state(self):
        async def failing(*args):
            if args == ("version",):
                return "Mullvad VPN client 2026.4"
            raise MullvadError("failed", "Mullvad query failed.")
        result = await Mullvad(runner=failing).status()
        self.assertEqual(result["state"], "erro")
        self.assertEqual(result["error"], "daemon_down")
        self.assertFalse(result["mullvad"]["daemon"])
        self.assertIsNone(result["lan"])

    async def test_unsupported_version_is_stable_error(self):
        fake = FakeCli({("version",): "Mullvad VPN client 2025.6"})
        with self.assertRaises(MullvadError) as raised:
            await Mullvad(runner=fake).status()
        self.assertEqual(raised.exception.code, "version_unsupported")

    async def test_enable_emits_all_states_and_verifies_before_protected(self):
        events = []
        fake = EnableCli()
        client = Mullvad(runner=fake, event_sink=lambda event, data: events.append((event, data)))
        self.assertEqual(await client.enable(confirm=True), {"accepted": True})
        self.assertEqual([data["state"] for _, data in events], ["ativando", "ativando", "ativando", "ativando", "ativando", "protegido"])
        self.assertTrue(all(event == "security.state" for event, _ in events))
        self.assertNotIn("hostname", events[-1][1].get("relay", {}))
        self.assertEqual(fake.calls.count(("connect",)), 1)
        self.assertNotIn("autoConnect", client._snapshot)
        await client.close()

    async def test_enable_country_is_validated_and_selected(self):
        client = Mullvad(runner=EnableCli())
        await client.enable("al", confirm=True)
        self.assertIn(("relay", "set", "location", "al"), client._runner.calls)
        await client.close()
        with self.assertRaises(MullvadError) as raised:
            await Mullvad(runner=EnableCli()).enable("zz", confirm=True)
        self.assertEqual(raised.exception.code, "invalid")

    async def test_a_relay_outside_the_approved_list_is_replaced_and_the_users_is_restored(self):
        fake = RestoreCli(initial_connected=False)
        fake.relay = ["se"]  # the user's own choice, outside the approved list
        client = Mullvad(runner=fake)
        self.assertEqual(await client.enable(confirm=True), {"accepted": True})
        self.assertIn(fake.relay[0], PREFERRED_RELAYS)
        self.assertEqual(client._snapshot["relayRestriction"], ["se"])
        result = await client.restore(True)
        self.assertEqual(result["state"], "desativado")
        self.assertEqual(fake.relay, ["se"])
        await client.close()

    async def test_an_approved_relay_already_selected_is_left_alone(self):
        fake = RestoreCli()
        fake.relay = ["jp"]
        client = Mullvad(runner=fake)
        await client.enable(confirm=True)
        self.assertNotIn("relay_set", [call for call in fake.calls if call[:3] == ("relay", "set", "location")])
        self.assertEqual([call for call in fake.calls if call[:3] == ("relay", "set", "location")], [])
        await client.close()

    async def test_a_relay_changed_by_hand_meanwhile_is_preserved_on_restore(self):
        fake = RestoreCli()
        fake.relay = ["se"]
        client = Mullvad(runner=fake)
        await client.enable(confirm=True)
        fake.relay = ["ie"]
        result = await client.restore(True)
        self.assertEqual(result["state"], "desativado")
        self.assertNotIn("relay", result["restored"])
        self.assertEqual(fake.relay, ["ie"])
        await client.close()

    async def test_verified_connection_with_unknown_relay_is_partial(self):
        events = []
        client = Mullvad(runner=EnableCli(unverified=True), event_sink=lambda event, data: events.append(data))
        with self.assertRaises(MullvadError):
            await client.enable(confirm=True)
        self.assertEqual(events[-1]["state"], "falha_fechada")
        self.assertNotIn("protegido", [data["state"] for data in events])
        await client.close()

    async def test_protected_requires_lockdown_lan_and_dns_invariants(self):
        for bad in ("lan", "dns"):
            events = []
            client = Mullvad(runner=EnableCli(bad=bad), event_sink=lambda event, data: events.append(data))
            with self.assertRaises(MullvadError):
                await client.enable(confirm=True)
            self.assertEqual(events[-1]["state"], "falha_fechada")
            self.assertNotIn("protegido", [data["state"] for data in events])
            await client.close()

    async def test_every_post_lockdown_failure_is_fail_closed(self):
        for failure in (("lockdown-mode", "set", "on"), ("lan", "set", "block"), ("connect",)):
            events = []
            client = Mullvad(runner=EnableCli(fail=failure), event_sink=lambda event, data: events.append(data))
            with self.assertRaises(MullvadError):
                await client.enable(confirm=True)
            self.assertEqual(events[-1]["state"], "falha_fechada")
            self.assertNotIn("protegido", [data["state"] for data in events])
            if failure == ("lockdown-mode", "set", "on"):
                self.assertEqual(events[-1]["lockdown"], "unknown")
            await client.close()

    async def test_dns_custom_is_snapshot_only_and_capped_at_eight(self):
        client = Mullvad(runner=EnableCli())
        with self.assertRaises(MullvadError):
            await client._exec("dns_custom", None, *("1.1.1.1",) * 9)
        with self.assertRaises(MullvadError):
            await client._exec("dns_custom", None, "not-an-ip")
        client._snapshot = {"dnsServers": ("1.1.1.1",)}
        with self.assertRaises(MullvadError):
            await client._exec("dns_custom", None, "8.8.8.8")
        self.assertEqual(await client._exec("dns_custom", None, "1.1.1.1"), "")

    async def test_allowlist_has_only_read_only_vectors(self):
        fake = FakeCli({("version",): "2026.4"})
        client = Mullvad(runner=fake)
        await client.version()
        self.assertEqual(fake.calls, [("version",)])
        self.assertEqual(client.timeout, 10)
        self.assertEqual(set(ALLOWED), {"version", "account", "status", "relays", "lan", "dns", "split", "auto", "lockdown", "anti", "tunnel",
                                        "lockdown_on", "lockdown_off", "lan_block", "lan_allow", "dns_default", "dns_custom",
                                        "anti_set", "quantum_set", "daita_set",
                                        "relay_get", "relay_set", "relay_restore", "connect", "disconnect",
                                        "status_help", "relay_help", "lan_help", "dns_help", "split_help", "account_help",
                                        "split_delete", "split_add", "anti_help", "tunnel_help"})
        self.assertFalse(any(arg in {"sudo", "sh", "factory-reset", "reset-settings"}
                             for name, args in ALLOWED.items() if name not in {"disconnect"} for arg in args))
        self.assertEqual(ALLOWED["relay_set"], ("relay", "set", "location"))

    async def test_restore_complete_disconnects_and_unlocks_last(self):
        fake = RestoreCli()
        events = []
        client = Mullvad(runner=fake, event_sink=lambda event, data: events.append(data))
        await client.enable(confirm=True)
        result = await client.restore(True)
        self.assertEqual(result["state"], "desativado")
        self.assertFalse(result["lockdown"])
        self.assertFalse(events[-1]["lockdown"])
        self.assertFalse(fake.connected)
        self.assertFalse(fake.lockdown)
        self.assertIsNone(client._watch_task)
        self.assertLess(fake.calls.index(("disconnect",)), fake.calls.index(("lockdown-mode", "set", "off")))
        self.assertGreaterEqual(fake.calls.count(("status", "-j")), 5)

    async def test_restore_preserves_manual_change(self):
        fake = RestoreCli()
        client = Mullvad(runner=fake)
        await client.enable(confirm=True)
        fake.lan = "allow"
        result = await client.restore(True)
        self.assertEqual(result["state"], "erro")
        self.assertIn("lan", result["preserved"])
        self.assertFalse(fake.lockdown)

    async def test_restore_does_not_disconnect_preexisting_vpn(self):
        fake = RestoreCli(initial_connected=True)
        client = Mullvad(runner=fake)
        await client.enable(confirm=True)
        result = await client.restore(True)
        self.assertEqual(result["state"], "desativado")
        self.assertTrue(fake.connected)
        self.assertNotIn(("disconnect",), fake.calls)

    async def test_restore_failure_keeps_lockdown(self):
        fake = RestoreCli()
        client = Mullvad(runner=fake)
        await client.enable(confirm=True)
        fake.fail = ("lan", "set", "allow")
        result = await client.restore(True)
        self.assertEqual(result["state"], "erro")
        self.assertTrue(fake.lockdown)
        self.assertTrue(result["lockdown"])

    async def test_restore_from_fail_closed_tunnel_drop_is_restorable(self):
        fake = RestoreCli()
        client = Mullvad(runner=fake)
        await client.enable(confirm=True)
        client._state = "falha_fechada"
        fake.connected = False
        result = await client.restore(True)
        self.assertEqual(result["state"], "desativado")
        self.assertNotIn(("disconnect",), fake.calls[fake.calls.index(("connect",)) + 1:])

    async def test_restore_without_snapshot_is_manual_fail_closed(self):
        events = []
        client = Mullvad(event_sink=lambda event, data: events.append(data))
        result = await client.restore(True)
        self.assertEqual(result, {"manual": True, "state": "falha_fechada", "lockdown": "unknown"})
        self.assertEqual(events[-1]["state"], "falha_fechada")

    async def test_restore_requires_explicit_confirmation(self):
        with self.assertRaises(MullvadError) as raised:
            await Mullvad().restore(False)
        self.assertEqual(raised.exception.code, "invalid")

    async def test_restore_lockdown_failure_keeps_lockdown(self):
        fake = RestoreCli()
        client = Mullvad(runner=fake)
        await client.enable(confirm=True)
        fake.fail = ("lockdown-mode", "set", "off")
        result = await client.restore(True)
        self.assertEqual(result["state"], "erro")
        self.assertTrue(fake.lockdown)
