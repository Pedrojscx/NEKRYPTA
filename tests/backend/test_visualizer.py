import asyncio
import math
import shutil
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from helpers import visualizer
from helpers.backend import Backend, BackendError, DEFAULTS, check_setting


class FakeStream:
    def __init__(self, lines=()):
        self.lines = list(lines)
        self.waiting = asyncio.Event()

    async def readline(self):
        if self.lines:
            return self.lines.pop(0)
        await self.waiting.wait()
        return b""


class FakeProcess:
    def __init__(self, lines=(), wait_hangs=False):
        self.stdout = FakeStream(lines)
        self.signals = []
        self.killed = False
        self.wait_calls = 0
        self.wait_hangs = wait_hangs
        self.released = asyncio.Event()

    def send_signal(self, value):
        self.signals.append(value)
        if value == visualizer.signal.SIGTERM and not self.wait_hangs:
            self.released.set()

    def kill(self):
        self.killed = True
        self.released.set()

    async def wait(self):
        self.wait_calls += 1
        if self.wait_hangs and self.wait_calls == 1:
            await self.released.wait()
        return 0


class Clock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value


class VisualizerTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_stop_reference_count_uses_one_process(self):
        process = FakeProcess()
        with patch.object(shutil, "which", return_value="/usr/bin/cava"), \
                patch.object(asyncio, "create_subprocess_exec", new=AsyncMock(return_value=process)) as spawn:
            client = visualizer.Visualizer()
            self.assertEqual(await client.start(), {"accepted": True})
            self.assertEqual(await client.start(), {"accepted": True})
            self.assertEqual(spawn.await_count, 1)
            self.assertEqual(await client.stop(), {"stopped": False})
            self.assertEqual(await client.stop(), {"stopped": True})
            self.assertEqual(process.signals, [visualizer.signal.SIGTERM])
            self.assertGreaterEqual(process.wait_calls, 1)

    async def test_silence_emits_sem_audio_for_zero_bands(self):
        events = []
        client = visualizer.Visualizer(event_sink=lambda event, data: events.append((event, data)))
        self.assertTrue(client._frame(";".join(["0"] * 32)))
        self.assertEqual(events[-1], ("visualizer.state", {
            "available": True, "enabled": False, "state": "sem_audio", "source": "cava",
            "bands": 32, "errorCode": None, "errorMessage": None}))

    async def test_missing_cava_is_dependency_missing(self):
        events = []
        with patch.object(shutil, "which", return_value=None), \
                patch.object(asyncio, "create_subprocess_exec", new=AsyncMock()) as spawn:
            client = visualizer.Visualizer(event_sink=lambda event, data: events.append((event, data)))
            self.assertEqual(await client.start(), {"accepted": False})
            status = await client.status()
        self.assertEqual(status["state"], "indisponivel")
        self.assertEqual(status["errorCode"], "dependency_missing")
        spawn.assert_not_awaited()
        self.assertEqual(events[-1][0], "visualizer.state")

    async def test_process_failure_is_reported(self):
        with patch.object(shutil, "which", return_value="/usr/bin/cava"), \
                patch.object(asyncio, "create_subprocess_exec", new=AsyncMock(side_effect=OSError("missing"))):
            with self.assertRaises(visualizer.VisualizerError) as raised:
                await visualizer.Visualizer().start()
        self.assertEqual(raised.exception.code, "process_failed")

    async def test_invalid_frames_are_discarded_and_valid_frames_are_throttled(self):
        clock = Clock()
        events = []
        client = visualizer.Visualizer(event_sink=lambda event, data: events.append((event, data)), clock=clock)
        invalid = ";".join(["nan"] * 32)
        self.assertFalse(client._frame(invalid))
        line = ";".join(["500"] * 32)
        self.assertTrue(client._frame(line))
        self.assertFalse(client._frame(line))
        clock.value += 1 / 30
        self.assertTrue(client._frame(line))
        frames = [data for event, data in events if event == "visualizer.frame"]
        self.assertEqual(len(frames), 2)
        self.assertEqual(frames[0]["levels"], [0.5] * 32)
        self.assertEqual(frames[0]["energy"], 0.5)

    async def test_restart_is_limited_to_three_attempts_in_sixty_seconds(self):
        processes = [FakeProcess(lines=[b""]) for _ in range(4)]
        events = []
        with patch.object(shutil, "which", return_value="/usr/bin/cava"), \
                patch.object(asyncio, "create_subprocess_exec", new=AsyncMock(side_effect=processes)) as spawn:
            client = visualizer.Visualizer(event_sink=lambda event, data: events.append((event, data)))
            await client.start()
            for _ in range(12):
                await asyncio.sleep(0)
            status = await client.status()
            self.assertEqual(spawn.await_count, 4)
            self.assertEqual(status["state"], "erro")
            self.assertEqual(status["errorCode"], "restart_limited")
            self.assertTrue(any(event == "visualizer.state" and data["state"] == "erro" for event, data in events))
            await client.close()

    async def test_stop_escalates_sigterm_to_sigkill_and_waits(self):
        process = FakeProcess(wait_hangs=True)
        with patch.object(shutil, "which", return_value="/usr/bin/cava"), \
                patch.object(asyncio, "create_subprocess_exec", new=AsyncMock(return_value=process)):
            client = visualizer.Visualizer()
            await client.start()
            await client.stop()
        self.assertEqual(process.signals, [visualizer.signal.SIGTERM])
        self.assertTrue(process.killed)
        self.assertGreaterEqual(process.wait_calls, 2)
        self.assertIsNone(client.process)

    async def test_close_cleans_up_child(self):
        process = FakeProcess()
        with patch.object(shutil, "which", return_value="/usr/bin/cava"), \
                patch.object(asyncio, "create_subprocess_exec", new=AsyncMock(return_value=process)):
            client = visualizer.Visualizer()
            await client.start()
            await client.close()
        self.assertIsNone(client.process)
        self.assertEqual(client.demand, 0)
        self.assertTrue(process.signals)

    async def test_backend_dispatch_and_settings_contract(self):
        self.assertFalse(DEFAULTS["visualizer"])
        self.assertTrue(DEFAULTS["barAmbient"])
        self.assertEqual(DEFAULTS["barAmbientStyle"], "reflexo-azul")
        check_setting("visualizer", True)
        # The ambient layer is part of the fixed look: it cannot be changed through the helper.
        for key, value in (("visualizer", 1), ("barAmbient", False), ("barAmbientStyle", "outro")):
            with self.assertRaises(BackendError):
                check_setting(key, value)
        instance = Backend()
        with patch.object(instance.visualizer, "start", new=AsyncMock(return_value={"accepted": True})) as start:
            self.assertEqual(await instance.dispatch("visualizer.start", {}), {"accepted": True})
            start.assert_awaited_once()
        await instance.close()

    def test_cava_config_is_monitor_only_and_thirty_hz(self):
        config = (Path(__file__).resolve().parents[2] / "assets/cava-visualizer.conf").read_text()
        self.assertIn("framerate = 30", config)
        self.assertIn("method = pipewire", config)
        self.assertIn("source = auto", config)
        self.assertNotIn("microphone", config.lower())


if __name__ == "__main__":
    unittest.main()
