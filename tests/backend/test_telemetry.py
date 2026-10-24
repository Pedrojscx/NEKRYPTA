import unittest
from unittest.mock import AsyncMock

from helpers import backend


class TelemetryTests(unittest.IsolatedAsyncioTestCase):
    def test_projection_marks_first_cpu_sample_unavailable(self):
        result = backend.telemetry_projection({
            "cpu": {"available": True, "usage": 0.0},
            "memory": {"available": True, "total": 8 * 1073741824, "used": 2 * 1073741824},
            "recorded": {"cpu": None, "memory": None},
        })
        self.assertEqual(result["cpu"], {"state": "indisponivel", "percent": None})
        self.assertEqual(result["memory"]["state"], "disponivel")
        self.assertEqual(result["memory"]["percent"], 25.0)
        self.assertEqual(result["memory"]["usedGiB"], 2.0)
        self.assertEqual(result["memory"]["totalGiB"], 8.0)

    def test_projection_uses_delta_cpu_and_bounds_values(self):
        result = backend.telemetry_projection({
            "cpu": {"available": True, "usage": 137.8},
            "memory": {"available": False, "total": 0, "used": 0},
            "recorded": {"cpu": 42.0},
        })
        self.assertEqual(result["cpu"], {"state": "disponivel", "percent": 100.0})
        self.assertEqual(result["memory"]["state"], "indisponivel")
        self.assertIsNone(result["memory"]["percent"])

    async def test_dispatch_reuses_the_existing_sampler(self):
        instance = backend.Backend()
        sample = {
            "cpu": {"available": True, "usage": 33.3},
            "memory": {"available": True, "total": 4 * 1073741824, "used": 1 * 1073741824},
            "recorded": {"cpu": 33.3},
        }
        instance.sysmon.sample = AsyncMock(return_value=sample)
        result = await instance.dispatch("system.telemetry", {})
        instance.sysmon.sample.assert_awaited_once_with(history=False)
        self.assertEqual(result["cpu"]["percent"], 33.3)
        await instance.close()


if __name__ == "__main__":
    unittest.main()
