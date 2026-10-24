import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "helpers"))
import backend
import mullvad
from test_mullvad import RestoreCli


class HangingProcess:
    def __init__(self):
        self.returncode = None
        self.killed = False
        self.waited = False
        self._never = asyncio.Event()

    async def communicate(self, data=None):
        await self._never.wait()

    def kill(self):
        self.killed = True

    async def wait(self):
        self.waited = True


class GatedRestoreCli(RestoreCli):
    def __init__(self):
        super().__init__()
        self.block_next_status = False
        self.status_entered = asyncio.Event()
        self.release_status = asyncio.Event()

    async def __call__(self, *args):
        if args == ("status", "-j") and self.block_next_status:
            self.block_next_status = False
            self.status_entered.set()
            await self.release_status.wait()
        return await super().__call__(*args)


class IntegrationGate5Tests(unittest.IsolatedAsyncioTestCase):
    async def test_files_search_reports_fd_missing_without_running_a_process(self):
        instance = backend.Backend()
        with patch.object(backend.shutil, "which", return_value=None), self.assertRaises(backend.BackendError) as raised:
            await instance.dispatch("files.search", {"query": "notes"})
        self.assertEqual(raised.exception.code, "unavailable")

    async def test_dbus_import_missing_is_stable_unavailable_error(self):
        instance = backend.Backend()
        with patch.dict(sys.modules, {"dbus_next": None, "dbus_next.aio": None}):
            with self.assertRaises(backend.BackendError) as raised:
                await instance.interface("org.example", "/", "org.example.Interface")
        self.assertEqual(raised.exception.code, "unavailable")

    async def test_run_timeout_kills_and_waits_for_child(self):
        process = HangingProcess()
        with patch.object(backend.shutil, "which", return_value="/fixture/tool"), \
                patch.object(backend.asyncio, "create_subprocess_exec", new=AsyncMock(return_value=process)):
            with self.assertRaises(backend.BackendError) as raised:
                await backend.run("fixture-tool", timeout=0.001)
        self.assertEqual(raised.exception.code, "timeout")
        self.assertTrue(process.killed)
        self.assertTrue(process.waited)

    async def test_mullvad_exec_timeout_kills_and_waits_for_child(self):
        process = HangingProcess()
        client = mullvad.Mullvad(timeout=10)
        with patch.object(mullvad.shutil, "which", return_value="/fixture/mullvad"), \
                patch.object(mullvad.asyncio, "create_subprocess_exec", new=AsyncMock(return_value=process)):
            with self.assertRaises(mullvad.MullvadError) as raised:
                await client._exec("version", timeout=0.001)
        self.assertEqual(raised.exception.code, "timeout")
        self.assertTrue(process.killed)
        self.assertTrue(process.waited)

    async def test_restore_preserves_explicit_manual_dns_relay_and_tunnel_changes(self):
        for key in ("lan", "tunnel"):
            fake = RestoreCli()
            client = mullvad.Mullvad(runner=fake)
            await client.enable(confirm=True)
            if key == "lan":
                fake.lan = "allow"
            else:
                fake.connected = False
            result = await client.restore(True)
            self.assertEqual(result["state"], "erro", key)
            self.assertIn(key, result["preserved"], key)
            self.assertFalse(result["lockdown"], key)
            await client.close()

    async def test_restore_fails_closed_when_each_readback_is_not_confirmed(self):
        cases = (("lan", False), ("tunnel", False), ("lockdown", False))
        for key, initially_connected in cases:
            fake = RestoreCli(initial_connected=initially_connected, readback_fail=key)
            client = mullvad.Mullvad(runner=fake)
            await client.enable(confirm=True)
            fake.restore_phase = True
            result = await client.restore(True)
            self.assertEqual(result["state"], "erro", key)
            self.assertIn(key, result["incompleteRestore"], key)
            self.assertTrue(result["lockdown"], key)
            await client.close()

    async def test_watcher_is_cancelled_before_restore_changes_state(self):
        fake = RestoreCli()
        client = mullvad.Mullvad(runner=fake)
        await client.enable(confirm=True)
        watcher = client._watch_task
        self.assertIsNotNone(watcher)
        result = await client.restore(True)
        self.assertEqual(result["state"], "desativado")
        self.assertTrue(watcher.done())
        self.assertIsNone(client._watch_task)

    async def test_enable_waits_until_restore_finishes_without_interleaving(self):
        fake = GatedRestoreCli()
        client = mullvad.Mullvad(runner=fake)
        await client.enable(confirm=True)
        fake.block_next_status = True
        restore_task = asyncio.create_task(client.restore(True))
        await fake.status_entered.wait()
        enable_task = asyncio.create_task(client.enable(confirm=True))
        await asyncio.sleep(0)
        self.assertFalse(enable_task.done())
        fake.release_status.set()
        restore_result = await restore_task
        enable_result = await enable_task
        self.assertEqual(restore_result["state"], "desativado")
        self.assertEqual(enable_result, {"accepted": True})
        off = fake.calls.index(("lockdown-mode", "set", "off"))
        on_after = next(index for index, call in enumerate(fake.calls) if index > off and call == ("lockdown-mode", "set", "on"))
        self.assertLess(off, on_after)
        await client.close()
