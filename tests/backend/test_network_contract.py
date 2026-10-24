import unittest
from unittest.mock import AsyncMock, patch

from helpers import backend


class NetworkContractTests(unittest.IsolatedAsyncioTestCase):
    def test_state_mapping_covers_wifi_cable_and_connectivity(self):
        self.assertEqual(backend.wifi_state(100, True, "full"), "conectado")
        self.assertEqual(backend.wifi_state(100, True, "limited"), "sem_internet")
        self.assertEqual(backend.wifi_state(60, True, "none"), "conectando")
        self.assertEqual(backend.wifi_state(30, True, "none"), "desconectado")
        self.assertEqual(backend.wifi_state(100, False, "none"), "wifi_desativado")
        self.assertEqual(backend.wifi_state(100, True, "full", "cabo"), "cabo")

    def test_connect_vectors_have_no_password_or_shell(self):
        self.assertEqual(backend.wifi_connect_args("Rede conhecida", True),
                         ("nmcli", "connection", "up", "id", "Rede conhecida"))
        self.assertEqual(backend.wifi_connect_args("Rede protegida", False),
                         ("nmcli", "device", "wifi", "connect", "Rede protegida"))
        with self.assertRaises(backend.BackendError):
            backend.wifi_connect_args("Rede\nmaliciosa", False)

    def test_local_ip_is_masked(self):
        self.assertEqual(backend.mask_local_ip("192.168.15.23/24"), "192.168.x.x")
        self.assertIsNone(backend.mask_local_ip("not-an-ip"))

    async def test_nm_absent_is_a_stable_state(self):
        instance = backend.Backend()
        instance.interface = AsyncMock(side_effect=backend.BackendError("unavailable", "NetworkManager ausente."))
        with patch("helpers.backend.run", side_effect=backend.BackendError("unavailable", "nmcli ausente.")):
            result = await instance.dispatch("wifi.list", {})
        self.assertEqual(result["state"], "nm_indisponivel")
        self.assertEqual(result["type"], "nenhum")
        await instance.close()

    async def test_list_falls_back_to_nmcli_without_dbus_next(self):
        calls = []

        async def fake_run(*args, **kwargs):
            calls.append(args)
            if args[:3] == ("nmcli", "-t", "radio"):
                return b"enabled\n"
            if args[:5] == ("nmcli", "-t", "-f", "CONNECTIVITY", "general"):
                return b"full\n"
            if args[:5] == ("nmcli", "-t", "-f", "NAME,TYPE", "connection"):
                return b"Casa:802-11-wireless\n"
            if args[:4] == ("nmcli", "-t", "-f", "IN-USE,SSID,SECURITY,SIGNAL,DEVICE"):
                return b"*:Casa:WPA2:83:wlan0\n:Aberta:--:20:wlan0\n"
            return b"GENERAL.DEVICE:wlan0\nGENERAL.TYPE:wifi\nGENERAL.STATE:100 (connected)\nGENERAL.CONNECTION:Casa\nIP4.ADDRESS[1]:192.168.1.5/24\n"

        instance = backend.Backend()
        with patch.dict("sys.modules", {"dbus_next": None}), patch("helpers.backend.run", new=fake_run):
            result = await instance.dispatch("wifi.list", {})
        self.assertEqual(result["state"], "conectado")
        self.assertEqual(result["ssid"], "Casa")
        self.assertEqual(result["ip"], "192.168.x.x")
        self.assertEqual(result["networks"][0]["security"], "protegida")
        self.assertTrue(any(call[:4] == ("nmcli", "-t", "-f", "GENERAL.DEVICE,GENERAL.TYPE,GENERAL.STATE,GENERAL.CONNECTION,IP4.ADDRESS")
                            for call in calls))
        await instance.close()


if __name__ == "__main__":
    unittest.main()
