"""Optional CAVA music visualizer owned by the backend."""
import asyncio
import math
import os
from pathlib import Path
import shutil
import signal
import time


CONFIG = Path(__file__).resolve().parents[1] / "assets" / "cava-visualizer.conf"
BANDS = 32
FRAME_INTERVAL = 1 / 30
RESTART_LIMIT = 3
RESTART_WINDOW = 60


class VisualizerError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def _state(available, enabled=False, state="desativado", error_code=None, error_message=None):
    return {"available": bool(available), "enabled": bool(enabled), "state": state, "source": "cava" if available else None,
            "bands": BANDS, "errorCode": error_code, "errorMessage": error_message}


class Visualizer:
    def __init__(self, binary="cava", config=CONFIG, event_sink=None, clock=None):
        self.binary = binary
        self.config = str(config)
        self.event_sink = event_sink
        self.clock = clock or time.monotonic
        self.lock = asyncio.Lock()
        self.demand = 0
        self.process = None
        self._reader_task = None
        self._stopping = False
        self._restart_times = []
        self._last_frame = None
        self._state = _state(False)

    def _available(self):
        return shutil.which(self.binary) is not None

    def _emit(self, event, data):
        if self.event_sink is not None:
            self.event_sink(event, data)

    def _set_state(self, state, *, error_code=None, error_message=None):
        available = self._available()
        next_state = _state(available, self.demand > 0 and self.process is not None, state, error_code, error_message)
        if next_state == self._state:
            return
        self._state = next_state
        self._emit("visualizer.state", dict(next_state))

    @staticmethod
    def parse_frame(line):
        text = line.decode(errors="replace") if isinstance(line, bytes) else str(line)
        values = [item.strip() for item in text.strip().split(";")]
        if len(values) != BANDS:
            return None
        try:
            values = [float(item) for item in values]
        except (TypeError, ValueError):
            return None
        if not all(math.isfinite(value) and 0 <= value <= 1000 for value in values):
            return None
        levels = [value / 1000 for value in values]
        return {"levels": levels, "energy": sum(levels) / BANDS}

    def _frame(self, line):
        frame = self.parse_frame(line)
        if frame is None:
            return False
        now = self.clock()
        if self._last_frame is not None and now - self._last_frame < FRAME_INTERVAL:
            return False
        self._last_frame = now
        self._emit("visualizer.frame", frame)
        self._set_state("sem_audio" if frame["energy"] == 0 else "reproduzindo")
        return True

    async def _spawn_locked(self):
        try:
            self.process = await asyncio.create_subprocess_exec(
                self.binary, "-p", self.config, stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError as error:
            self.process = None
            raise VisualizerError("process_failed", "CAVA could not be started.") from error
        self._stopping = False
        self._last_frame = None
        self._reader_task = asyncio.create_task(self._read_process(self.process))

    async def start(self):
        async with self.lock:
            if not self._available():
                self.demand = 0
                self._set_state("indisponivel", error_code="dependency_missing", error_message="CAVA não está instalado.")
                return {"accepted": False}
            self.demand += 1
            if self.process is not None:
                self._set_state("ativo")
                return {"accepted": True}
            self._set_state("carregando")
            try:
                await self._spawn_locked()
            except VisualizerError as error:
                self.demand = 0
                self._set_state("erro", error_code=error.code, error_message=error.message)
                raise
            # O backend não conhece o estado MPRIS. Enquanto aguarda o primeiro
            # frame, o frontend pode combinar este estado com o player.
            self._set_state("aguardando_reproducao")
            return {"accepted": True}

    async def stop(self):
        async with self.lock:
            if self.demand > 0:
                self.demand -= 1
            if self.demand:
                return {"stopped": False}
            process = self.process
            if process is None:
                self._set_state("desativado")
                return {"stopped": True}
            self._stopping = True
            self._set_state("encerrando")
            await self._terminate_locked(process)
            self.process = None
            reader = self._reader_task
            self._reader_task = None
            if reader is not None and not reader.done():
                reader.cancel()
                await asyncio.gather(reader, return_exceptions=True)
            self._set_state("desativado")
            return {"stopped": True}

    async def _terminate_locked(self, process):
        try:
            process.send_signal(signal.SIGTERM)
        except (AttributeError, ProcessLookupError):
            pass
        try:
            await asyncio.wait_for(process.wait(), 2)
            return
        except asyncio.TimeoutError:
            pass
        try:
            process.kill()
        except ProcessLookupError:
            pass
        await process.wait()

    async def _read_process(self, process):
        try:
            while process.stdout is not None:
                line = await process.stdout.readline()
                if not line:
                    break
                self._frame(line)
            returncode = await process.wait()
        except asyncio.CancelledError:
            return
        async with self.lock:
            if process is not self.process:
                return
            self.process = None
            self._reader_task = None
            if self._stopping or self.demand == 0:
                self._set_state("desativado")
                return
            now = self.clock()
            self._restart_times = [stamp for stamp in self._restart_times if now - stamp < RESTART_WINDOW]
            if len(self._restart_times) >= RESTART_LIMIT:
                self._set_state("erro", error_code="restart_limited", error_message="CAVA restarted too many times.")
                return
            self._restart_times.append(now)
            try:
                await self._spawn_locked()
            except VisualizerError as error:
                self._set_state("erro", error_code=error.code, error_message=error.message)
                return
            self._set_state("aguardando_reproducao")

    async def status(self):
        async with self.lock:
            available = self._available()
            if not available and self.process is None:
                self._state = _state(False, False, "indisponivel", "dependency_missing", "CAVA não está instalado.")
            return dict(self._state)

    async def close(self):
        async with self.lock:
            self.demand = 0
            process = self.process
            if process is not None:
                self._stopping = True
                self._set_state("encerrando")
                await self._terminate_locked(process)
                self.process = None
            reader = self._reader_task
            self._reader_task = None
            if reader is not None and not reader.done():
                reader.cancel()
                await asyncio.gather(reader, return_exceptions=True)
            self._set_state("desativado")
