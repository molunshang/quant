"""BacktestExecutor: runs backtests in a thread pool, batch-wait for results."""
from __future__ import annotations

import concurrent.futures
import itertools
from dataclasses import dataclass, field

from api.runner import run_backtest

_job_ids = itertools.count(1)


@dataclass
class BacktestJob:
    id: int
    symbol: str
    params: dict
    status: str = "running"
    result: dict | None = None
    error: str | None = None


class BacktestExecutor:
    def __init__(self, initial_cash: float = 100_000.0, max_workers: int = 4):
        self.initial_cash = initial_cash
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
        self._jobs: dict[int, BacktestJob] = {}
        self._futures: list[concurrent.futures.Future] = []
        self._order: list[int] = []

    def submit(self, symbol, strategy_ref, params=None, freq="daily", start="2020-01-01",
               end="2024-12-31", adjust="qfq") -> int:
        job_id = next(_job_ids)
        job = BacktestJob(id=job_id, symbol=symbol, params=params or {})
        self._jobs[job_id] = job
        self._order.append(job_id)
        fut = self._pool.submit(
            run_backtest,
            symbol=symbol,
            strategy_ref=strategy_ref,
            params=params,
            freq=freq,
            start=start,
            end=end,
            adjust=adjust,
            initial_cash=self.initial_cash,
        )
        self._futures.append(fut)
        return job_id

    def wait_all(self, timeout: float = 300.0) -> list[dict]:
        """Block until all submitted jobs in the current batch finish. Returns results in submission order."""
        if not self._futures:
            return []
        for fut in concurrent.futures.as_completed(self._futures, timeout=timeout):
            # Find which job this future belongs to by index.
            idx = self._futures.index(fut)
            job_id = self._order[idx]
            job = self._jobs[job_id]
            try:
                job.result = fut.result()
                job.status = "done"
            except Exception as e:  # noqa: BLE001 - surface per-job errors
                job.error = str(e)
                job.status = "error"
        results = [self._jobs[job_id] for job_id in self._order]
        self._futures = []
        self._order = []
        self._jobs = {}
        return [
            {
                "job_id": j.id,
                "symbol": j.symbol,
                "params": j.params,
                "status": j.status,
                "result": j.result,
                "error": j.error,
            }
            for j in results
        ]

    def reset_batch(self):
        """Clear any remaining batch state (safe no-op if already drained)."""
        self._futures = []
        self._order = []
        self._jobs = {}

    def shutdown(self):
        self._pool.shutdown(wait=True)
