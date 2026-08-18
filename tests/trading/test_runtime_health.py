import importlib
import importlib.util
import sqlite3
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from backend.config import Settings
from backend.trading.migrations import MIGRATION_VERSIONS, run_migrations


def _health_module():
    assert importlib.util.find_spec("backend.trading.health") is not None
    return importlib.import_module("backend.trading.health")


def _protect_router_runtime(monkeypatch, router_mod):
    for name in (
        "runtime_provider",
        "trading_repo",
        "pool_service",
        "market_data_service",
        "data_job_service",
        "account_service",
        "execution_service",
        "portfolio_service",
        "strategy_service",
        "plan_service",
        "backtest_service",
        "review_service",
    ):
        monkeypatch.setattr(router_mod, name, getattr(router_mod, name, None), raising=False)


def test_check_trading_schema_reports_unmigrated_database(tmp_path):
    health = _health_module()
    db_path = str(tmp_path / "unmigrated.db")
    sqlite3.connect(db_path).close()

    report = health.check_trading_schema(db_path)

    expected_versions = [item["version"] for item in MIGRATION_VERSIONS]
    assert report["status"] == "error"
    assert report["expected_version"] == max(expected_versions)
    assert report["applied_version"] == 0
    assert report["missing_versions"] == expected_versions
    assert "trade_calendar" in report["missing_tables"]


def test_check_trading_schema_ok_after_current_migrations(tmp_path):
    health = _health_module()
    db_path = str(tmp_path / "ready.db")
    run_migrations(db_path)

    report = health.check_trading_schema(db_path)

    assert report == {
        "status": "ok",
        "expected_version": max(
            item["version"] for item in MIGRATION_VERSIONS
        ),
        "applied_version": max(
            item["version"] for item in MIGRATION_VERSIONS
        ),
        "expected_migrations": [
            {"version": item["version"], "name": item["name"]}
            for item in MIGRATION_VERSIONS
        ],
        "applied_migrations": [
            {"version": item["version"], "name": item["name"]}
            for item in MIGRATION_VERSIONS
        ],
        "missing_versions": [],
        "unexpected_versions": [],
        "name_mismatches": [],
        "missing_tables": [],
        "missing_columns": {},
    }


def test_check_trading_schema_rejects_damaged_schema(tmp_path):
    health = _health_module()
    db_path = str(tmp_path / "damaged.db")
    run_migrations(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP TABLE trade_calendar")
        conn.commit()

    report = health.check_trading_schema(db_path)

    assert report["status"] == "error"
    assert report["missing_versions"] == []
    assert report["missing_tables"] == ["trade_calendar"]


def test_check_trading_schema_rejects_unknown_migration_version(tmp_path):
    health = _health_module()
    db_path = str(tmp_path / "forward-schema.db")
    run_migrations(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO trade_migrations(version, name) VALUES(?, ?)",
            (999, "foreign_migration"),
        )
        conn.commit()

    report = health.check_trading_schema(db_path)

    assert report["status"] == "error"
    assert report["unexpected_versions"] == [999]
    assert report["name_mismatches"] == []
    assert report["applied_migrations"][-1] == {
        "version": 999,
        "name": "foreign_migration",
    }


def test_check_trading_schema_rejects_migration_name_mismatch(tmp_path):
    health = _health_module()
    db_path = str(tmp_path / "foreign-schema.db")
    run_migrations(db_path)
    expected_name = MIGRATION_VERSIONS[0]["name"]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE trade_migrations SET name=? WHERE version=?",
            ("foreign_name", MIGRATION_VERSIONS[0]["version"]),
        )
        conn.commit()

    report = health.check_trading_schema(db_path)

    assert report["status"] == "error"
    assert report["unexpected_versions"] == []
    assert report["name_mismatches"] == [
        {
            "version": MIGRATION_VERSIONS[0]["version"],
            "expected_name": expected_name,
            "applied_name": "foreign_name",
        }
    ]


@pytest.mark.asyncio
async def test_lifespan_migrates_empty_db_and_builds_one_web_runtime(
    monkeypatch, tmp_path
):
    import backend.main as main_mod
    import backend.trading.router as router_mod

    db_path = str(tmp_path / "web-runtime.db")
    fake_provider = object()
    provider_calls = []
    _protect_router_runtime(monkeypatch, router_mod)
    monkeypatch.setattr(main_mod, "db", main_mod.db)
    monkeypatch.setattr(main_mod.settings, "db_path", db_path)
    monkeypatch.setattr(
        router_mod,
        "get_provider",
        lambda configured: provider_calls.append(configured) or fake_provider,
    )

    async with main_mod.lifespan(main_mod.app):
        assert main_mod.db.db_path == db_path
        assert router_mod.trading_repo.db_path == db_path
        assert router_mod.runtime_provider is fake_provider
        assert router_mod.market_data_service.provider is fake_provider
        assert (
            router_mod.data_job_service.market_data_service
            is router_mod.market_data_service
        )
        assert router_mod.plan_service.market_data_service.provider is fake_provider

    assert provider_calls == [main_mod.settings]
    assert _health_module().check_trading_schema(db_path)["status"] == "ok"


@pytest.mark.asyncio
async def test_lifespan_propagates_migration_failure(monkeypatch):
    import backend.main as main_mod

    def fail_migration(path):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(main_mod, "run_migrations", fail_migration, raising=False)

    with pytest.raises(sqlite3.OperationalError, match="disk I/O"):
        async with main_mod.lifespan(main_mod.app):
            pass


@pytest.mark.asyncio
async def test_health_returns_503_for_unmigrated_database(
    monkeypatch, tmp_path
):
    import backend.main as main_mod

    db_path = str(tmp_path / "empty.db")
    sqlite3.connect(db_path).close()
    monkeypatch.setattr(main_mod.settings, "db_path", db_path)

    async with AsyncClient(
        transport=ASGITransport(app=main_mod.app), base_url="http://test"
    ) as client:
        response = await client.get("/api/health")

    assert response.status_code == 503
    assert response.json()["db"] == "ok"
    assert response.json()["trading_schema"]["status"] == "error"


@pytest.mark.asyncio
async def test_health_returns_503_for_corrupt_database(monkeypatch, tmp_path):
    import backend.main as main_mod

    db_path = tmp_path / "corrupt.db"
    db_path.write_bytes(b"not a sqlite database")
    monkeypatch.setattr(main_mod.settings, "db_path", str(db_path))

    async with AsyncClient(
        transport=ASGITransport(app=main_mod.app), base_url="http://test"
    ) as client:
        response = await client.get("/api/health")

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert response.json()["db"] == "error"


def test_scheduler_uses_same_runtime_contract_and_db_path(monkeypatch, tmp_path):
    import backend.trading.jobs.scheduler as scheduler_mod
    import backend.trading.router as router_mod
    from backend.trading.jobs.runner import SchedulerRunner
    from backend.trading.services.data_job_service import DataJobService

    fake_provider = object()
    fake_settings = SimpleNamespace(
        db_path=str(tmp_path / "shared.db"),
        trading_data_max_missing_ratio=0.03,
        trading_benchmark_codes="000300.SH,000905.SH",
    )
    _protect_router_runtime(monkeypatch, router_mod)
    monkeypatch.setattr(scheduler_mod, "settings", fake_settings)
    monkeypatch.setattr(
        scheduler_mod, "get_provider", lambda configured: fake_provider
    )

    router_mod.configure_runtime(fake_settings, provider=fake_provider)
    runner = scheduler_mod.build_runner()

    assert isinstance(runner, SchedulerRunner)
    assert isinstance(runner.data_job_service, DataJobService)
    assert router_mod.trading_repo.db_path == runner.repo.db_path
    assert runner.repo.db_path == fake_settings.db_path
    assert runner.provider is fake_provider
    assert router_mod.runtime_provider is fake_provider


@pytest.mark.parametrize(
    "field",
    (
        "trading_scheduler_poll_seconds",
        "trading_stale_job_seconds",
        "trading_job_lock_ttl_seconds",
    ),
)
def test_scheduler_timing_settings_must_be_positive(field):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: 0})


def test_scheduler_timing_settings_are_environment_overridable(monkeypatch):
    monkeypatch.setenv("TRADING_SCHEDULER_POLL_SECONDS", "11")
    monkeypatch.setenv("TRADING_STALE_JOB_SECONDS", "1200")
    monkeypatch.setenv("TRADING_JOB_LOCK_TTL_SECONDS", "600")

    configured = Settings(_env_file=None)

    assert configured.trading_scheduler_poll_seconds == 11
    assert configured.trading_stale_job_seconds == 1200
    assert configured.trading_job_lock_ttl_seconds == 600
