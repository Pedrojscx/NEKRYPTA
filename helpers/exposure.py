"""Read-only local listener audit using ss."""
import asyncio
import datetime
import re
import shutil


class ExposureError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def _scope(address):
    host = address.lower().strip("[]").split("%", 1)[0]
    if host in {"*", "::", "0.0.0.0"}:
        return "all"
    if host == "::1" or host.startswith("127."):
        return "loopback"
    return "lan"


def _endpoint(value):
    value = value.strip()
    if value.startswith("[") and "]" in value:
        host, port = value[1:].split("]", 1)
        port = port.lstrip(":")
    else:
        host, port = value.rsplit(":", 1)
    if not port.isdigit():
        return None
    return host, int(port)


def _listener(line):
    fields = line.split()
    if len(fields) < 5 or fields[0].lower() not in {"tcp", "udp"}:
        return None
    endpoint = _endpoint(fields[4])
    if endpoint is None:
        return None
    address, port = endpoint
    process, pid = "", None
    match = re.search(r'users:\(\("([^"\r\n]+)",pid=(\d+)', line)
    if match:
        process, pid = match.group(1), int(match.group(2))
    return {"proto": fields[0].lower(), "address": address, "port": port, "scope": _scope(address),
            "process": process, "pid": pid}


async def audit(timeout=5, runner=None):
    args = ("-H", "-tulpn")
    if runner is not None:
        output = await runner(*args)
        text = output.decode(errors="replace") if isinstance(output, bytes) else str(output)
    else:
        if not shutil.which("ss"):
            raise ExposureError("unavailable", "The ss utility is not installed.")
        process = await asyncio.create_subprocess_exec("ss", *args, stdin=asyncio.subprocess.DEVNULL,
                                                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            output, _stderr = await asyncio.wait_for(process.communicate(), timeout)
        except asyncio.TimeoutError:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            raise ExposureError("timeout", "Listener audit timed out.") from None
        if process.returncode:
            raise ExposureError("failed", "Listener audit failed.")
        text = output.decode(errors="replace")
    return {"listeners": [item for line in text.splitlines() if (item := _listener(line)) is not None],
            "at": datetime.datetime.now().astimezone().isoformat(timespec="seconds")}
