"""Tests for crash logging — init, excepthook, faulthandler wiring."""
from __future__ import annotations

import sys
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

import faulthandler

from data.crash_log import init_crash_logging


def test_init_creates_log_dir_and_file():
    path = init_crash_logging()
    p = Path(path)
    assert p.name == "crash.log"
    assert p.parent.name == "logs"
    assert p.exists()


def test_rotating_handler_configured():
    init_crash_logging()
    root = logging.getLogger()
    handlers = [h for h in root.handlers if isinstance(h, RotatingFileHandler)]
    assert handlers, "root logger should have a RotatingFileHandler"
    h = handlers[0]
    assert h.maxBytes == 10 * 1024 * 1024
    assert h.backupCount == 5
    assert h.encoding == "utf-8"


def test_excepthook_writes_traceback(tmp_path, monkeypatch):
    init_crash_logging()
    log_path = Path(init_crash_logging())
    # avoid writing to the shared crash.log during tests; route to tmp
    import data.crash_log as mod
    orig_handler = logging.getLogger().handlers[0]
    monkeypatch.setattr(mod, "_initialized", False)

    def boom():
        raise ValueError("boom test")

    try:
        boom()
    except ValueError:
        sys.excepthook(*sys.exc_info())

    content = log_path.read_text(encoding="utf-8")
    assert "ValueError: boom test" in content
    assert "boom test" in content


def test_excepthook_preserves_default(capsys):
    """The custom hook must still run the builtin default (not swallow stderr)."""
    init_crash_logging()
    try:
        raise RuntimeError("preserve-default")
    except RuntimeError:
        sys.excepthook(*sys.exc_info())
    err = capsys.readouterr().err
    assert "RuntimeError: preserve-default" in err


def test_faulthandler_enabled():
    init_crash_logging()
    assert faulthandler.is_enabled()


def test_dump_traceback_writes_to_file():
    init_crash_logging()
    path = Path(init_crash_logging())
    handler = logging.getLogger().handlers[0]
    faulthandler.dump_traceback(file=handler.stream, all_threads=True)
    handler.flush()
    assert "Current thread" in path.read_text(encoding="utf-8")
