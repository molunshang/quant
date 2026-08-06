"""Precache service: proactively download bars into the local cache so
backtests read local CSV instead of hitting the network on first use."""
from __future__ import annotations

import concurrent.futures
import itertools
import logging
import threading
from dataclasses import dataclass
from datetime import datetime

from .registry import get_registry
from .sources import CACHE_DIR, DataLayer

_job_ids = itertools.count(1)


@dataclass
class PrecacheJob:
    id: int
    symbol: str
    freq: str
    adjust: str
    start: str
    end: str
    status: str = "pending"  # pending|running|done|error
    progress: int = 0
    error: str | None = None
    created_at: str = ""


class PrecacheManager:
    def __init__(self, max_workers: int = 4, cache_dir: str = CACHE_DIR):
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
        self._jobs: dict[int, PrecacheJob] = {}
        self._lock = threading.Lock()
        self._futures: list[concurrent.futures.Future] = []
        self._dl = DataLayer(cache=True)
        self.cache_dir = cache_dir

    @property
    def cache_dir(self) -> str:
        return self._dl.cache_dir

    @cache_dir.setter
    def cache_dir(self, value: str) -> None:
        self._dl.cache_dir = value

    def _work(self, job: PrecacheJob):
        with self._lock:
            job.status = "running"
        try:
            reg = get_registry()
            info = reg.get(job.symbol)
            df = self._dl.get_bars(info, freq=job.freq, start=job.start, end=job.end, adjust=job.adjust, force=True)
            if df is None or df.empty:
                raise ValueError(f"no data for {job.symbol}")
            with self._lock:
                job.status = "done"
                job.progress = 100
        except Exception as e:  # noqa: BLE001 - per-job error
            logging.exception("precache job %s (%s) failed", job.id, job.symbol)
            with self._lock:
                job.status = "error"
                job.error = str(e)

    def submit(self, symbols, freq="daily", start="2020-01-01", end="2024-12-31", adjust="qfq"):
        ids = []
        for s in symbols:
            job_id = next(_job_ids)
            job = PrecacheJob(id=job_id, symbol=s, freq=freq, adjust=adjust,
                              start=start, end=end,
                              created_at=datetime.now().isoformat(timespec="seconds"))
            with self._lock:
                self._jobs[job_id] = job
            ids.append(job_id)
            fut = self._pool.submit(self._work, job)
            with self._lock:
                self._futures.append(fut)
        return ids

    def get(self, job_id):
        with self._lock:
            j = self._jobs.get(job_id)
            return j.__dict__ if j else None

    def list(self):
        with self._lock:
            return [j.__dict__ for j in sorted(self._jobs.values(), key=lambda x: x.id)]

    def wait_all(self, timeout: float = 120.0):
        with self._lock:
            futures = list(self._futures)
        for fut in concurrent.futures.as_completed(futures, timeout=timeout):
            pass
        with self._lock:
            self._futures = []

    def refresh_all(self):
        """Force re-download every cached symbol over its full cached range and
        overwrite. Raw history never changes, so this is safe and idempotent."""
        import os
        from datetime import date
        today = date.today().isoformat()
        for fn in os.listdir(self._dl.cache_dir):
            if not fn.endswith(".csv"):
                continue
            parts = fn.replace(".csv", "").split("_")
            if len(parts) != 4:
                continue
            typ, code, freq, adjust = parts
            # start from cached file's first date (parse from CSV) if available
            start = "2020-01-01"
            path = os.path.join(self._dl.cache_dir, fn)
            try:
                import pandas as pd
                first = pd.read_csv(path, usecols=["date"], nrows=1)["date"].iloc[0]
                start = str(first)[:10]
            except Exception as e:  # noqa: BLE001 - fall back to default start
                print(f"[precache] refresh_all: could not read first date from {fn}: {type(e).__name__}: {e}; using default start {start}")
            self.submit([code], freq=freq, start=start, end=today, adjust=adjust)

    def shutdown(self):
        self._pool.shutdown(wait=True)


manager = PrecacheManager()
