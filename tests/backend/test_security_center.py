import asyncio
import json
import unittest
from unittest.mock import patch

from helpers.security import SecurityCenter, SecurityError, classify_listener, pipewire_media_probe


class MullvadMock:
    def __init__(self, status):
        self.value = status
        self.lock = asyncio.Lock()

    async def status(self):
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


def connected(**changes):
    value = {
        "lockdown": True, "lan": "block", "dns": "default",
        "mullvad": {"daemon": True, "tunnelState": "connected", "loggedIn": True,
                    "exitIpMasked": "198.51.100.xxx"},
        "tunnel": True, "relay": {"country": "Albania", "city": "Tirana", "protocol": "wireguard",
                                    "hostname": "al-tia-wg-001", "country_code": "al"},
        "incomingRelay": None, "splitTunnel": {"enabled": False, "apps": 0},
    }
    value.update(changes)
    return value


class ExposureMock:
    async def audit(self):
        return {"listeners": [], "at": "fixture"}


class SecurityCenterTests(unittest.IsolatedAsyncioTestCase):
    async def test_activate_and_deactivate_require_confirm_true(self):
        center = SecurityCenter(MullvadMock(connected()), ExposureMock())
        for operation in (center.activate, center.deactivate):
            with self.assertRaises(SecurityError) as raised:
                await operation(confirm=False)
            self.assertEqual(raised.exception.code, "permission_denied")

    async def test_inactive_disconnected_mode_is_not_failed_and_external_agrees(self):
        status = connected(lockdown=False, tunnel=False, relay=None,
                           mullvad={"daemon": True, "tunnelState": "disconnected", "loggedIn": True})
        center = SecurityCenter(MullvadMock(status), ExposureMock(),
                                external_fetch=lambda: asyncio.sleep(0, result={"mullvad_exit_ip": False}))
        result = await center.verify()
        self.assertEqual(result["state"], "desativado")
        self.assertEqual(result["data"]["external"]["status"], "confirmado")
        self.assertNotIn("failed", result["state"])

    async def test_split_count_is_derived_from_parsed_items(self):
        status = connected(splitTunnel={"enabled": True, "apps": 1, "items": []})
        result = await SecurityCenter(MullvadMock(status), ExposureMock()).split_list()
        self.assertEqual(result["data"]["count"], 0)
        self.assertEqual(result["data"]["items"], [])

    async def test_pipewire_devices_are_not_reported_as_capture(self):
        fixture = json.dumps([
            {"id": 59, "info": {"props": {"media.class": "Audio/Source", "node.name": "alsa_input.analog-stereo"}}},
            {"id": 68, "info": {"props": {"media.class": "Video/Source", "node.name": "v4l2_input.usb"}}},
        ])
        with patch("helpers.security._exec_readonly", return_value=fixture):
            self.assertEqual(await pipewire_media_probe(), [])

    async def test_external_ip_is_masked_and_sensitive_location_is_not_logged(self):
        events = []
        center = SecurityCenter(MullvadMock(connected()), ExposureMock(), event_sink=lambda _, data: events.append(data),
                                external_fetch=lambda: asyncio.sleep(0, result={"ip": "179.130.12.34", "mullvad_exit_ip": False,
                                                                                 "country": "Brazil", "city": "Jacareí",
                                                                                 "organization": "Mullvad VPN"}))
        result = await center.verify()
        self.assertEqual(result["data"]["external"]["ipMasked"], "179.130.x.x")
        logged = json.dumps(events, ensure_ascii=False)
        self.assertNotIn("Brazil", logged)
        self.assertNotIn("Jacareí", logged)
        self.assertNotIn("Mullvad VPN", logged)

    async def test_overview_has_mullvad_lan_split_and_external_schema(self):
        center = SecurityCenter(MullvadMock(connected()), ExposureMock(), external_fetch=lambda: None)
        center.external_fetch = lambda: asyncio.sleep(0, result={"mullvad_exit_ip": True, "country": "SE"})
        result = await center.overview()
        self.assertIn("mullvad", result["data"])
        self.assertIn("lan", result["data"])
        self.assertIn("split", result["data"])
        self.assertTrue(result["data"]["mullvad"]["isMullvad"])

    async def test_overview_never_claims_protected_with_inconclusive_dns(self):
        center = SecurityCenter(
            MullvadMock(connected()), ExposureMock(),
            external_fetch=lambda: asyncio.sleep(0, result={"mullvad_exit_ip": True}),
            media_probe=lambda: asyncio.sleep(0, result=[]),
        )
        with patch.object(center, "dns_audit", return_value={
            "state": "warning", "stale": True, "data": {
                "result": "inconclusivo", "local": {"servers": ["100.100.100.100", "192.168.15.1"]},
                "external": {"result": "nao_executado"}}, "warnings": []}):
            result = await center.overview()
        self.assertEqual(result["state"], "partial")
        self.assertNotEqual(result["state"], "protected")

    async def test_external_offline_and_unknown_mullvad_are_not_safe(self):
        async def offline():
            raise SecurityError("timeout", "indisponível", True)
        center = SecurityCenter(MullvadMock(connected()), ExposureMock(), external_fetch=offline)
        result = await center.verify()
        self.assertEqual(result["state"], "partial")
        self.assertTrue(result["stale"])
        self.assertIsNone(result["data"]["local"]["isMullvad"])

    async def test_daemon_missing_is_unavailable(self):
        center = SecurityCenter(MullvadMock(RuntimeError("missing")), ExposureMock())
        with self.assertRaises(SecurityError) as raised:
            await center.verify()
        self.assertEqual(raised.exception.code, "verification_failed")

    async def test_ports_levels_include_evidence_and_guidance(self):
        class E:
            async def audit(self):
                return {"listeners": [
                    {"scope": "loopback", "address": "127.0.0.1", "port": 1, "proto": "tcp", "pid": 1, "process": "x"},
                    {"scope": "all", "address": "0.0.0.0", "port": 2, "proto": "tcp", "pid": None, "process": None},
                ]}
        result = await SecurityCenter(MullvadMock(connected()), E()).ports_list()
        self.assertEqual(result["data"]["items"][0]["level"], "baixo")
        self.assertEqual(result["data"]["items"][1]["level"], "atencao_elevada")
        self.assertIn("evidence", result["data"]["items"][1])

    async def test_dns_result_and_official_url_are_explicit(self):
        result = await SecurityCenter(MullvadMock(connected()), ExposureMock()).dns_audit(True)
        self.assertIn(result["data"]["result"], {"nao_executado", "inconclusivo", "indisponivel"})
        self.assertEqual(result["data"]["external"]["officialUrl"], "https://mullvad.net/en/check")

    async def test_split_lan_and_media_schemas(self):
        center = SecurityCenter(MullvadMock(connected()), ExposureMock(), media_probe=lambda: asyncio.sleep(0, result=[]))
        split = await center.split_list()
        lan = await center.lan_get()
        media = await center.media_status()
        self.assertEqual(split["data"]["count"], 0)
        self.assertEqual(set(lan["data"]), {"state", "value", "services"})
        self.assertEqual(media["data"]["items"], [])

    async def test_media_revoke_requires_confirm_and_normalizes_result(self):
        center = SecurityCenter(MullvadMock(connected()), ExposureMock(), permission_store=lambda _: asyncio.sleep(0, result={"revoked": True, "verified": True}))
        result = await center.media_revoke("pipewire:1", True)
        self.assertEqual(result["data"], {"id": "pipewire:1", "revoked": True, "futureOnly": True, "verified": True})
        with self.assertRaises(SecurityError) as raised:
            await center.media_revoke("pipewire:1", False)
        self.assertEqual(raised.exception.code, "permission_denied")

    async def test_listener_unknown_is_not_presented_as_safe(self):
        self.assertEqual(classify_listener({"scope": "unknown"})[0], "nao_verificado")


if __name__ == "__main__":
    unittest.main()
