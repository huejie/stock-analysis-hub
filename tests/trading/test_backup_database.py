import sqlite3

from backend.trading.jobs import backup_database


def _create_source_db(path):
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE sample (value INTEGER NOT NULL)")
        conn.execute("INSERT INTO sample VALUES (1)")


def test_run_backup_checks_backup_file_integrity(tmp_path):
    source = tmp_path / "source.db"
    _create_source_db(source)

    result = backup_database.run_backup(
        str(source), str(tmp_path / "backups")
    )

    assert result["path"]
    assert result["integrity_ok"] is True
    assert result["integrity_result"] == ["ok"]


def test_run_backup_preserves_failed_integrity_evidence(monkeypatch, tmp_path):
    source = tmp_path / "source.db"
    _create_source_db(source)
    real_connect = backup_database.sqlite3.connect
    connect_count = 0

    class FailedIntegrityConnection:
        def __init__(self, connection):
            self.connection = connection

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            self.connection.close()

        def execute(self, sql, *args, **kwargs):
            if sql.strip().upper() == "PRAGMA INTEGRITY_CHECK":
                return [("page 2 is never used",)]
            return self.connection.execute(sql, *args, **kwargs)

    def connect(path, *args, **kwargs):
        nonlocal connect_count
        connect_count += 1
        connection = real_connect(path, *args, **kwargs)
        if connect_count == 3:
            return FailedIntegrityConnection(connection)
        return connection

    monkeypatch.setattr(backup_database.sqlite3, "connect", connect)

    result = backup_database.run_backup(
        str(source), str(tmp_path / "backups")
    )

    assert result["path"]
    assert result["integrity_ok"] is False
    assert result["integrity_result"] == ["page 2 is never used"]
