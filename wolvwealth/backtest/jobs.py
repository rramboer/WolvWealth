"""Background execution of backtests with pollable progress.

A JobManager runs report-producing work on a small thread pool and keeps
each job's status, progress and result in memory so clients can poll for it.
Transport-agnostic: owners are opaque strings and the work is any callable
that accepts a ProgressCallback.

Jobs live in process memory, so every poll must reach the process that
accepted the job. That holds for the default single gunicorn worker; more
workers need a shared store or sticky routing.
"""

import concurrent.futures
import dataclasses
import enum
import logging
import secrets
import threading
import time
from collections.abc import Callable

from wolvwealth.backtest.progress import Progress, ProgressCallback
from wolvwealth.backtest.service import BacktestReport

logger = logging.getLogger(__name__)

Work = Callable[[ProgressCallback], BacktestReport]


class JobStatus(enum.StrEnum):
    """Lifecycle of a job."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"

    @property
    def finished(self) -> bool:
        """True once the job will not change again."""
        return self in (JobStatus.SUCCEEDED, JobStatus.FAILED)


class JobLimitError(Exception):
    """The job was refused because of capacity limits.

    `scope` is "owner" when this owner has too many jobs in flight (retry
    after one finishes) and "server" when the whole queue is full.
    """

    def __init__(self, message: str, scope: str) -> None:
        """Record which limit was hit."""
        super().__init__(message)
        self.scope = scope


@dataclasses.dataclass
class _Job:
    id: str
    owner: str
    include_equity: bool
    created_at: float
    status: JobStatus = JobStatus.QUEUED
    progress: Progress | None = None
    started_at: float | None = None
    finished_at: float | None = None
    report: BacktestReport | None = None
    report_dict: dict | None = None
    error: str | None = None
    cached: bool = False


class JobManager:
    """Thread-safe registry and runner of backtest jobs."""

    def __init__(
        self,
        *,
        max_workers: int = 2,
        max_pending: int = 20,
        max_active_per_owner: int = 2,
        ttl_seconds: float = 900,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """Configure capacity limits and how long finished jobs stay pollable."""
        self.max_workers = max_workers
        self.max_pending = max_pending
        self.max_active_per_owner = max_active_per_owner
        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self._jobs: dict[str, _Job] = {}
        self._futures: dict[str, concurrent.futures.Future] = {}
        self._lock = threading.Lock()
        self._executor: concurrent.futures.ThreadPoolExecutor | None = None  # Created on first use (fork-safe).

    def submit(
        self,
        owner: str,
        work: Work,
        *,
        include_equity: bool = True,
        on_accept: Callable[[], None] | None = None,
    ) -> dict:
        """Queue `work` and return the new job's snapshot.

        `on_accept` runs after the capacity checks pass and before the job is
        created, while the registry is locked. Use it to charge the caller:
        if it raises, no job is created and the exception propagates.
        """
        with self._lock:
            self._prune_locked()
            self._check_capacity_locked(owner)
            if on_accept is not None:
                on_accept()
            job = self._new_job_locked(owner, include_equity)
            if self._executor is None:
                self._executor = concurrent.futures.ThreadPoolExecutor(
                    max_workers=self.max_workers, thread_name_prefix="backtest"
                )
            self._futures[job.id] = self._executor.submit(self._run, job, work)
            return self._snapshot_locked(job)

    def complete(
        self,
        owner: str,
        report: BacktestReport,
        *,
        include_equity: bool = True,
        on_accept: Callable[[], None] | None = None,
    ) -> dict:
        """Register an already-available report (a cache hit) as a finished job."""
        report_dict = report.to_dict(include_equity=include_equity)  # Serialize outside the lock.
        with self._lock:
            self._prune_locked()
            if on_accept is not None:
                on_accept()
            job = self._new_job_locked(owner, include_equity)
            now = self._clock()
            job.started_at = job.finished_at = now
            job.cached = True
            self._succeed_locked(job, report, report_dict)
            return self._snapshot_locked(job)

    def check_capacity(self, owner: str) -> None:
        """Raise JobLimitError if a submit from `owner` would be refused right now.

        A cheap pre-check so callers can refuse before doing validation work;
        submit() repeats it atomically with the charge, so this is advisory.
        """
        with self._lock:
            self._prune_locked()
            self._check_capacity_locked(owner)

    def get(self, job_id: str, owner: str) -> dict | None:
        """Snapshot of the job, or None if it does not exist, expired, or belongs to someone else."""
        with self._lock:
            self._prune_locked()
            job = self._jobs.get(job_id)
            if job is None or job.owner != owner:
                return None
            return self._snapshot_locked(job)

    def wait(self, job_id: str, timeout: float | None = None) -> bool:
        """Block until the job finishes. Returns False on timeout. For tests and shutdown."""
        with self._lock:
            future = self._futures.get(job_id)
        if future is None:
            return True
        try:
            future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            return False
        return True

    def shutdown(self, wait: bool = True) -> None:
        """Stop the worker pool."""
        with self._lock:
            executor, self._executor = self._executor, None
        if executor is not None:
            executor.shutdown(wait=wait)

    # Internals. Methods ending in _locked require self._lock to be held.

    def _run(self, job: _Job, work: Work) -> None:
        with self._lock:
            job.status = JobStatus.RUNNING
            job.started_at = self._clock()

        def on_progress(progress: Progress) -> None:
            with self._lock:
                job.progress = progress

        # Every failure, including in serialization, must end the job; otherwise
        # it would report "running" forever and count against the owner's limit.
        try:
            report = work(on_progress)
            report_dict = report.to_dict(include_equity=job.include_equity)  # Serialize outside the lock.
        except ValueError as err:  # Expected, user-facing failures (bad data range etc.).
            self._fail(job, str(err))
        except Exception:
            logger.exception("Backtest job %s failed", job.id)
            self._fail(job, "Internal error while running the backtest.")
        else:
            with self._lock:
                job.finished_at = self._clock()
                self._succeed_locked(job, report, report_dict)

    def _fail(self, job: _Job, message: str) -> None:
        with self._lock:
            job.status = JobStatus.FAILED
            job.error = message
            job.finished_at = self._clock()
            self._futures.pop(job.id, None)

    def _succeed_locked(self, job: _Job, report: BacktestReport, report_dict: dict) -> None:
        job.status = JobStatus.SUCCEEDED
        job.report = report
        job.report_dict = report_dict
        if job.progress is not None:
            job.progress = Progress("done", job.progress.total, job.progress.total, "Done")
        else:
            job.progress = Progress("done", 1, 1, "Done")
        self._futures.pop(job.id, None)

    def _new_job_locked(self, owner: str, include_equity: bool) -> _Job:
        job = _Job(id=secrets.token_urlsafe(12), owner=owner, include_equity=include_equity, created_at=self._clock())
        self._jobs[job.id] = job
        return job

    def _check_capacity_locked(self, owner: str) -> None:
        active = [job for job in self._jobs.values() if not job.status.finished]
        if len(active) >= self.max_pending:
            raise JobLimitError("The backtest queue is full. Try again in a minute.", scope="server")
        mine = sum(1 for job in active if job.owner == owner)
        if mine >= self.max_active_per_owner:
            raise JobLimitError(
                f"You already have {mine} backtest(s) running. Wait for one to finish.",
                scope="owner",
            )

    def _prune_locked(self) -> None:
        cutoff = self._clock() - self.ttl_seconds
        expired = [
            job_id
            for job_id, job in self._jobs.items()
            if job.status.finished and job.finished_at is not None and job.finished_at < cutoff
        ]
        for job_id in expired:
            del self._jobs[job_id]

    def _snapshot_locked(self, job: _Job) -> dict:
        now = self._clock()
        elapsed = None
        if job.started_at is not None:
            elapsed = (job.finished_at or now) - job.started_at
        remaining = None
        if job.status == JobStatus.RUNNING and job.progress is not None and elapsed is not None:
            fraction = job.progress.fraction
            if fraction > 0:
                remaining = elapsed / fraction * (1 - fraction)
        snapshot = {
            "job_id": job.id,
            "status": str(job.status),
            "progress": job.progress.to_dict() if job.progress else None,
            "elapsed_seconds": None if elapsed is None else round(elapsed, 2),
            "estimated_seconds_remaining": None if remaining is None else round(remaining, 1),
            "cached": job.cached,
        }
        if job.status == JobStatus.SUCCEEDED:
            snapshot["report"] = job.report_dict
        if job.status == JobStatus.FAILED:
            snapshot["error"] = job.error
        return snapshot
