import pytest
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call

from scripts.migrate_to_pg import (
    extract_sqlite_table_data,
    build_upsert_query,
    run_migration,
    TABLES_TO_MIGRATE,
)


def test_extract_sqlite_data_populated():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT)")
        conn.execute("INSERT INTO users (id, email) VALUES (1, 'test@test.com')")
        conn.commit()
        conn.close()

        cols, rows = extract_sqlite_table_data(str(db_path), "users")
        assert cols == ["id", "email"]
        assert len(rows) == 1
        assert rows[0][1] == "test@test.com"
        assert rows[0]["email"] == "test@test.com"
        assert isinstance(rows[0], dict)
        assert rows[0] == {"id": 1, "email": "test@test.com"}


def test_extract_sqlite_data_empty_table():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, title TEXT)")
        conn.commit()
        conn.close()

        cols, rows = extract_sqlite_table_data(str(db_path), "items")
        assert cols == ["id", "title"]
        assert rows == []


def test_extract_sqlite_data_nonexistent_table():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        conn = sqlite3.connect(db_path)
        conn.commit()
        conn.close()

        cols, rows = extract_sqlite_table_data(str(db_path), "nonexistent")
        assert cols == []
        assert rows == []


def test_build_upsert_query_with_id():
    columns = ["id", "email", "password_hash"]
    query = build_upsert_query("users", columns)
    expected = (
        "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3) "
        "ON CONFLICT (id) DO UPDATE SET email = EXCLUDED.email, password_hash = EXCLUDED.password_hash"
    )
    assert query == expected


def test_build_upsert_query_without_id():
    columns = ["key", "value"]
    query = build_upsert_query("settings", columns)
    expected = "INSERT INTO settings (key, value) VALUES ($1, $2) ON CONFLICT DO NOTHING"
    assert query == expected


def test_build_upsert_query_only_id():
    columns = ["id"]
    query = build_upsert_query("dummy", columns)
    expected = "INSERT INTO dummy (id) VALUES ($1) ON CONFLICT (id) DO NOTHING"
    assert query == expected


def test_build_upsert_query_empty_columns():
    assert build_upsert_query("empty", []) == ""


@pytest.mark.asyncio
async def test_run_migration_flow():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT)")
        conn.execute("INSERT INTO users (id, email) VALUES (1, 'user1@test.com'), (2, 'user2@test.com')")
        conn.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO tasks (id, name) VALUES (10, 'Task A')")
        conn.commit()
        conn.close()

        mock_conn = AsyncMock()
        mock_conn.fetchval = AsyncMock(return_value="users_id_seq")
        mock_conn.execute = AsyncMock()
        mock_conn.executemany = AsyncMock()

        counts = await run_migration(sqlite_path=str(db_path), pg_conn=mock_conn)

        assert isinstance(counts, dict)
        assert counts["users"] == 2
        assert counts["tasks"] == 1
        for tbl in TABLES_TO_MIGRATE:
            assert tbl in counts

        # Verify executemany was called for populated tables
        executed_tables = [call_args[0][0] for call_args in mock_conn.executemany.call_args_list]
        assert any("INSERT INTO users" in stmt for stmt in executed_tables)
        assert any("INSERT INTO tasks" in stmt for stmt in executed_tables)

        # Verify sequence reset was executed
        reset_stmts = [call_args[0][0] for call_args in mock_conn.execute.call_args_list]
        assert any("setval" in stmt and "users" in stmt for stmt in reset_stmts)


@pytest.mark.asyncio
async def test_run_migration_missing_pg_url(monkeypatch):
    from core import config
    monkeypatch.setattr(config, "DATABASE_URL", None)

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        conn = sqlite3.connect(db_path)
        conn.commit()
        conn.close()

        with pytest.raises(ValueError, match="DATABASE_URL"):
            await run_migration(sqlite_path=str(db_path), pg_url=None)


@pytest.mark.asyncio
async def test_run_migration_sqlite_file_not_found():
    with pytest.raises(FileNotFoundError, match="não encontrado"):
        await run_migration(
            sqlite_path="caminho_inexistente_para_teste_12345.db",
            pg_url="postgresql://user:pass@localhost:5432/test",
        )


def test_extract_sqlite_data_nonexistent_file():
    cols, rows = extract_sqlite_table_data("caminho_inexistente_98765.db", "users")
    assert cols == []
    assert rows == []


def test_row_dict_behaviors():
    from scripts.migrate_to_pg import RowDict

    rd = RowDict(["id", "name", "active"], (1, "Alice", True))
    assert rd["id"] == 1
    assert rd["name"] == "Alice"
    assert rd["active"] is True
    assert rd[0] == 1
    assert rd[1] == "Alice"
    assert rd[2] is True
    assert rd.get("name") == "Alice"
    assert rd.get("unknown", "default") == "default"
    assert list(rd.keys()) == ["id", "name", "active"]
    assert list(rd.values()) == [1, "Alice", True]
    assert rd == {"id": 1, "name": "Alice", "active": True}


def test_main_cli_execution(monkeypatch, capsys):
    from scripts.migrate_to_pg import main

    monkeypatch.setattr(
        "sys.argv",
        ["migrate_to_pg.py", "--sqlite", "data/cloner.db", "--pg-url", "postgresql://user:pass@localhost:5432/db"],
    )

    async def mock_run_migration(sqlite_path=None, pg_url=None, pg_conn=None):
        return {"users": 5, "accounts": 2, "tasks": 0}

    monkeypatch.setattr("scripts.migrate_to_pg.run_migration", mock_run_migration)

    main()
    captured = capsys.readouterr()
    assert "MIGRAÇÃO DE DADOS" in captured.out
    assert "users" in captured.out
    assert "5 registro(s)" in captured.out
    assert "Migração finalizada com sucesso" in captured.out

