"""Tests for backtest progress reporting and the background JobManager."""

import datetime
import threading

import pandas as pd
import pytest

from tests.conftest import FIXTURES_DIR
from wolvwealth.backtest import BacktestConfig, Frequency, MaxSharpeStrategy, run_backtest, run_report
from wolvwealth.backtest.jobs import JobLimitError, JobManager
from wolvwealth.backtest.progress import Progress


def fixture_prices() -> pd.DataFrame:
    return pd.read_csv(FIXTURES_DIR / "historical_prices.csv", parse_dates=True, index_col="Date")


def fixture_config() -> BacktestConfig:
    prices = fixture_prices()
    return BacktestConfig(
        start=datetime.date(2023, 7, 1),
        end=prices.index[-1].date(),
        universe=tuple(prices.columns),
        frequency=Frequency.QUARTERLY,
        lookback_days=120,
    )


class FakeReport:
    def __init__(self, label: str = "report") -> None:
        self.label = label

    def to_dict(self, include_equity: bool = True) -> dict:
        return {"label": self.label, "include_equity": include_equity}


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


class TestProgressReporting:
    def test_engine_reports_every_decision_and_completion(self):
        steps = []
        cfg = fixture_config()
        run_backtest(
            fixture_prices(), cfg, MaxSharpeStrategy(), on_step=lambda done, total: steps.append((done, total))
        )
        total = steps[0][1]
        assert total > 1
        assert [done for done, _ in steps] == list(range(total + 1))
        assert all(t == total for _, t in steps)

    def test_service_progress_covers_optimizer_then_benchmarks(self):
        updates: list[Progress] = []
        run_report(fixture_prices(), fixture_config(), benchmark_tickers=("NVDA",), on_progress=updates.append)
        phases = [update.phase for update in updates]
        assert phases[0] == "optimizer"
        assert phases[-1] == "benchmarks"
        assert phases.index("benchmarks") > phases.index("optimizer")
        fractions = [update.fraction for update in updates]
        assert fractions == sorted(fractions)
        assert fractions[-1] == 1.0
        assert updates[0].message.startswith("Optimizing rebalance 1 of")
        assert len({update.total for update in updates}) == 1  # total is stable, so the bar never jumps back


class TestJobManager:
    def test_submit_runs_work_and_returns_report(self):
        manager = JobManager(max_workers=1)
        snapshot = manager.submit("alice", lambda progress: FakeReport(), include_equity=False)
        assert snapshot["status"] in ("queued", "running")
        assert manager.wait(snapshot["job_id"], timeout=5)
        done = manager.get(snapshot["job_id"], "alice")
        assert done["status"] == "succeeded"
        assert done["report"] == {"label": "report", "include_equity": False}
        assert done["progress"]["fraction"] == 1.0
        manager.shutdown()

    def test_progress_and_eta_are_visible_mid_run(self):
        clock = FakeClock()
        manager = JobManager(max_workers=1, clock=clock)
        reached, release = threading.Event(), threading.Event()

        def work(progress):
            clock.now += 10  # ten seconds pass, a quarter of the work is done
            progress(Progress("optimizer", 1, 4, "Optimizing rebalance 2 of 3"))
            reached.set()
            release.wait(5)
            return FakeReport()

        job_id = manager.submit("alice", work)["job_id"]
        assert reached.wait(5)
        running = manager.get(job_id, "alice")
        assert running["status"] == "running"
        assert running["progress"]["completed"] == 1
        assert running["progress"]["message"] == "Optimizing rebalance 2 of 3"
        assert running["elapsed_seconds"] == 10
        assert running["estimated_seconds_remaining"] == 30  # 10s for 25% -> 30s left
        assert "report" not in running
        release.set()
        manager.wait(job_id, timeout=5)
        manager.shutdown()

    def test_jobs_are_private_to_their_owner(self):
        manager = JobManager(max_workers=1)
        job_id = manager.submit("alice", lambda progress: FakeReport())["job_id"]
        manager.wait(job_id, timeout=5)
        assert manager.get(job_id, "bob") is None
        assert manager.get("missing", "alice") is None
        manager.shutdown()

    def test_on_accept_failure_creates_no_job(self):
        manager = JobManager(max_workers=1)

        def refuse():
            raise PermissionError("out of uses")

        with pytest.raises(PermissionError):
            manager.submit("alice", lambda progress: FakeReport(), on_accept=refuse)
        assert manager._jobs == {}
        manager.shutdown()

    def test_per_owner_limit_is_checked_before_on_accept(self):
        manager = JobManager(max_workers=1, max_active_per_owner=1)
        release = threading.Event()
        first = manager.submit("alice", lambda progress: release.wait(5) and FakeReport())
        charges = []
        with pytest.raises(JobLimitError) as excinfo:
            manager.submit("alice", lambda progress: FakeReport(), on_accept=lambda: charges.append(1))
        assert excinfo.value.scope == "owner"
        assert charges == []  # refused jobs are never charged
        manager.submit("bob", lambda progress: FakeReport())  # other owners are unaffected
        release.set()
        manager.wait(first["job_id"], timeout=5)
        manager.shutdown()

    def test_server_queue_limit(self):
        manager = JobManager(max_workers=1, max_pending=1, max_active_per_owner=5)
        release = threading.Event()
        first = manager.submit("alice", lambda progress: release.wait(5) and FakeReport())
        with pytest.raises(JobLimitError) as excinfo:
            manager.submit("bob", lambda progress: FakeReport())
        assert excinfo.value.scope == "server"
        release.set()
        manager.wait(first["job_id"], timeout=5)
        manager.shutdown()

    def test_value_error_is_reported_and_other_errors_are_hidden(self):
        manager = JobManager(max_workers=1)

        def user_error(progress):
            raise ValueError("Fewer than two trading days")

        def bug(progress):
            raise RuntimeError("secret internals")

        user_job = manager.submit("alice", user_error)["job_id"]
        bug_job = manager.submit("alice", bug)["job_id"]
        manager.wait(user_job, timeout=5)
        manager.wait(bug_job, timeout=5)
        assert manager.get(user_job, "alice")["error"] == "Fewer than two trading days"
        hidden = manager.get(bug_job, "alice")
        assert hidden["status"] == "failed"
        assert "secret" not in hidden["error"]
        manager.shutdown()

    def test_failure_while_serializing_still_ends_the_job(self):
        """Regression: an exception after work() returned left the job 'running' forever."""
        manager = JobManager(max_workers=1, max_active_per_owner=1)
        job_id = manager.submit("alice", lambda progress: None)["job_id"]  # None has no to_dict()
        manager.wait(job_id, timeout=5)
        assert manager.get(job_id, "alice")["status"] == "failed"
        manager.submit("alice", lambda progress: FakeReport())  # the owner's slot was released
        manager.shutdown()

    def test_complete_registers_a_finished_cached_job(self):
        manager = JobManager()
        charges = []
        snapshot = manager.complete("alice", FakeReport("hit"), on_accept=lambda: charges.append(1))
        assert snapshot["status"] == "succeeded"
        assert snapshot["cached"] is True
        assert snapshot["report"]["label"] == "hit"
        assert charges == [1]
        assert manager._executor is None  # no thread pool needed for cache hits

    def test_finished_jobs_expire_after_ttl(self):
        clock = FakeClock()
        manager = JobManager(ttl_seconds=60, clock=clock)
        job_id = manager.complete("alice", FakeReport())["job_id"]
        clock.now += 59
        assert manager.get(job_id, "alice") is not None
        clock.now += 2
        assert manager.get(job_id, "alice") is None
