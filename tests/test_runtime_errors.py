"""Process-level Python error hooks should log and chain to runtime defaults."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_global_error_hooks_record_and_chain_without_swallowing():
    code = r'''
import asyncio
import sys
import threading
import types

sys.path.insert(0, ROOT)
reports = []
observability = types.ModuleType("observability")
observability.record_failure = lambda job, exc, logger=None: reports.append((job, str(exc)))
sys.modules["observability"] = observability

import runtime_errors
from config import Config
Config.ENV = "testing"
original_sys = sys.excepthook
original_thread = threading.excepthook
assert runtime_errors.install() is False
assert sys.excepthook is original_sys
assert threading.excepthook is original_thread

chained = []
sys.excepthook = lambda *args: chained.append("sys")
threading.excepthook = lambda args: chained.append("thread")
Config.ENV = "production"
assert runtime_errors.install(force=True) is True
assert runtime_errors.install(force=True) is False

exc = RuntimeError("uncaught sample")
sys.excepthook(type(exc), exc, exc.__traceback__)
threading.excepthook(types.SimpleNamespace(
    exc_type=type(exc), exc_value=exc, exc_traceback=exc.__traceback__,
    thread=threading.current_thread()))
assert chained == ["sys", "thread"]
assert [job for job, _ in reports] == [
    "runtime.uncaught_exception", "runtime.unhandled_thread_exception"]

loop = asyncio.new_event_loop()
async_chained = []
loop.set_exception_handler(lambda active, context: async_chained.append(context))
assert runtime_errors.install_asyncio_loop_handler(loop) is True
loop.call_exception_handler({"exception": RuntimeError("async sample")})
assert len(async_chained) == 1
assert reports[-1][0] == "runtime.unhandled_asyncio_exception"
loop.close()
'''.replace("ROOT", repr(str(ROOT)))
    completed = subprocess.run([sys.executable, "-c", code], capture_output=True,
                               text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr or completed.stdout
