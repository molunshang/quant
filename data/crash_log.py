"""Crash logging — native crashes (faulthandler) + uncaught Python exceptions.

Everything lands in data/logs/crash.log (rotating, utf-8) so any process
crash can be diagnosed afterwards from the log alone.
"""
from __future__ import annotations

import faulthandler
import logging
import os
import signal
import sys
import traceback
from logging.handlers import RotatingFileHandler

_LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "logs")
_LOG_FILE = os.path.join(_LOG_DIR, "crash.log")
_MAX_BYTES = 10 * 1024 * 1024
_BACKUP_COUNT = 5
_FORMAT = "%(asctime)s %(levelname)s %(message)s"

_initialized = False


def _excepthook(exc_type, exc_value, exc_tb):
    """Log uncaught exceptions, then pass through to the default hook."""
    logging.critical("Uncaught exception", exc_info=(exc_type, exc_value, exc_tb))
    sys.__excepthook__(exc_type, exc_value, exc_tb)


def init_crash_logging() -> str:
    """Install crash logging. Idempotent; returns the log file path."""
    global _initialized
    if _initialized:
        return _LOG_FILE

    os.makedirs(_LOG_DIR, exist_ok=True)
    handler = RotatingFileHandler(
        _LOG_FILE, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8"
    )
    logging.basicConfig(
        handlers=[handler], level=logging.WARNING,
        format=_FORMAT, force=True,
    )

    # Native crashes: SIGSEGV/ABRT/BUS/ILL/FPE dump all-threads stacks via enable().
    faulthandler.enable(file=handler.stream, all_threads=True)
    # SIGTRAP is the V8/py_mini_racer crash signal (exit 133); enable() does not
    # cover it, so register it explicitly to the same log file.
    faulthandler.register(signal.SIGTRAP, file=handler.stream, all_threads=True)

    sys.excepthook = _excepthook

    _initialized = True
    return _LOG_FILE
