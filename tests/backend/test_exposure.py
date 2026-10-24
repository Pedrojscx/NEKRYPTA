import unittest

from helpers.exposure import audit


class ExposureTests(unittest.IsolatedAsyncioTestCase):
    async def test_ss_fixture_classifies_scope_and_process(self):
        async def runner(*args):
            self.assertEqual(args, ("-H", "-tulpn"))
            return (
                'tcp LISTEN 0 128 127.0.0.1:631 0.0.0.0:* users:(("cupsd",pid=42,fd=7))\n'
                'tcp LISTEN 0 128 192.168.1.10:8080 0.0.0.0:* users:(("python",pid=99,fd=3))\n'
                'udp UNCONN 0 0 0.0.0.0:5353 0.0.0.0:* users:(("avahi-daemon",pid=77,fd=12))\n'
                'tcp LISTEN 0 128 [::1]:22 [::]:* users:(("sshd",pid=12,fd=4))\n'
            )
        result = await audit(runner=runner)
        self.assertEqual([item["scope"] for item in result["listeners"]], ["loopback", "lan", "all", "loopback"])
        self.assertEqual(result["listeners"][1]["pid"], 99)
        self.assertEqual(result["listeners"][0]["process"], "cupsd")

    async def test_non_numeric_and_unrelated_lines_are_ignored(self):
        async def runner(*args):
            return "Netid State Local Address:Port Peer Address:Port\ntcp LISTEN 0 1 *:http *:*\n"
        result = await audit(runner=runner)
        self.assertEqual(result["listeners"], [])
