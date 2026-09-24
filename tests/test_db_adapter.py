import pytest
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from core import config
from core.db_adapter import (
    convert_query_for_pg,
    is_pg_mode,
    close_db_pool,
    get_db_connection,
)


def test_convert_query_for_pg_multiple_placeholders():
    query = "SELECT * FROM users WHERE email = ? AND id = ? AND is_active = ?"
    expected = "SELECT * FROM users WHERE email = $1 AND id = $2 AND is_active = $3"
    assert convert_query_for_pg(query) == expected


def test_convert_query_for_pg_no_placeholders():
    query = "SELECT * FROM users ORDER BY id DESC"
    assert convert_query_for_pg(query) == query


def test_convert_query_for_pg_placeholder_in_single_quote_literal():
    query = "SELECT * FROM t WHERE name = 'who?' AND id = ?"
    expected = "SELECT * FROM t WHERE name = 'who?' AND id = $1"
    assert convert_query_for_pg(query) == expected


def test_convert_query_for_pg_placeholder_in_double_quote():
    query = 'SELECT "col?" FROM t WHERE id = ?'
    expected = 'SELECT "col?" FROM t WHERE id = $1'
    assert convert_query_for_pg(query) == expected


def test_convert_query_for_pg_escaped_single_quotes():
    query = "SELECT * FROM t WHERE text = 'don''t ask?' AND code = ?"
    expected = "SELECT * FROM t WHERE text = 'don''t ask?' AND code = $1"
    assert convert_query_for_pg(query) == expected


def test_convert_query_for_pg_comments():
    query = "-- question in comment ?\nSELECT * FROM t WHERE id = ? /* another ? */ AND active = ?"
    expected = "-- question in comment ?\nSELECT * FROM t WHERE id = $1 /* another ? */ AND active = $2"
    assert convert_query_for_pg(query) == expected


def test_is_pg_mode(monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", None)
    assert is_pg_mode() is False

    monkeypatch.setattr(config, "DATABASE_URL", "")
    assert is_pg_mode() is False

    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://user:pass@localhost:5432/db")
    assert is_pg_mode() is True


@pytest.mark.asyncio
async def test_close_db_pool_when_none():
    # Calling close_db_pool when no pool exists should not error
    await close_db_pool()


@pytest.mark.asyncio
async def test_get_db_connection_sqlite(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        test_db = Path(tmpdir) / "adapter_test.db"
        monkeypatch.setattr(config, "DATABASE_URL", None)
        monkeypatch.setattr(config, "DATABASE_PATH", test_db)

        async with get_db_connection() as db:
            await db.execute("""
                CREATE TABLE items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    value INTEGER NOT NULL
                )
            """)
            await db.commit()

            # Insert and test lastrowid
            cursor = await db.execute("INSERT INTO items (name, value) VALUES (?, ?)", ("first", 42))
            await db.commit()
            assert cursor.lastrowid == 1
            assert db.lastrowid == 1

            cursor2 = await db.execute("INSERT INTO items (name, value) VALUES (?, ?)", ("second", 84))
            await db.commit()
            assert cursor2.lastrowid == 2

            # Select with fetchone and row access via row["col"] and dict(row)
            cursor_sel = await db.execute("SELECT * FROM items WHERE id = ?", (1,))
            row = await cursor_sel.fetchone()
            assert row is not None
            assert row["id"] == 1
            assert row["name"] == "first"
            assert row["value"] == 42
            row_dict = dict(row)
            assert row_dict == {"id": 1, "name": "first", "value": 42}

            # Select with fetchall
            cursor_all = await db.execute("SELECT * FROM items ORDER BY id ASC")
            rows = await cursor_all.fetchall()
            assert len(rows) == 2
            assert rows[0]["name"] == "first"
            assert rows[1]["name"] == "second"
            assert [dict(r) for r in rows] == [
                {"id": 1, "name": "first", "value": 42},
                {"id": 2, "name": "second", "value": 84},
            ]


@pytest.mark.asyncio
async def test_get_db_connection_pg_mode(monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://user:pass@localhost:5432/db")

    mock_pool = MagicMock()
    mock_conn = MagicMock()
    mock_transaction = MagicMock()

    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    mock_pool.close = AsyncMock()

    mock_transaction.start = AsyncMock()
    mock_transaction.commit = AsyncMock()
    mock_transaction.rollback = AsyncMock()
    mock_conn.transaction.return_value = mock_transaction
    mock_conn.is_in_transaction.return_value = False

    class FakeRecord(dict):
        def __getitem__(self, item):
            return super().__getitem__(item)

    mock_conn.fetch = AsyncMock()
    mock_conn.execute = AsyncMock()

    with patch("core.db_adapter.get_pg_pool", AsyncMock(return_value=mock_pool)):
        async with get_db_connection() as db:
            # 1. Test INSERT auto appends RETURNING id and sets lastrowid
            mock_conn.fetch.return_value = [FakeRecord({"id": 101})]
            cursor = await db.execute("INSERT INTO users (email, name) VALUES (?, ?)", ("test@test.com", "Tester"))
            assert cursor.lastrowid == 101
            assert db.lastrowid == 101
            # Verify converted query and params passed to conn.fetch
            mock_conn.fetch.assert_called_once_with(
                "INSERT INTO users (email, name) VALUES ($1, $2) RETURNING id",
                "test@test.com",
                "Tester"
            )

            # Test commit commits active transaction
            await db.commit()
            mock_transaction.commit.assert_called_once()

            # 2. Test SELECT with fetchone and fetchall
            mock_conn.fetch.reset_mock()
            mock_conn.fetch.return_value = [
                FakeRecord({"id": 1, "email": "a@a.com"}),
                FakeRecord({"id": 2, "email": "b@b.com"}),
            ]
            cursor_sel = await db.execute("SELECT * FROM users WHERE is_active = ?", (1,))
            mock_conn.fetch.assert_called_once_with("SELECT * FROM users WHERE is_active = $1", 1)

            row = await cursor_sel.fetchone()
            assert row["id"] == 1
            assert dict(row) == {"id": 1, "email": "a@a.com"}

            rows = await cursor_sel.fetchall()
            assert len(rows) == 1
            assert rows[0]["id"] == 2

        # Test close_db_pool closes pool
        with patch("core.db_adapter._pg_pool", mock_pool):
            await close_db_pool()
            mock_pool.close.assert_called_once()


@pytest.mark.asyncio
async def test_pg_mode_insert_with_existing_returning(monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://user:pass@localhost:5432/db")

    mock_pool = MagicMock()
    mock_conn = MagicMock()
    mock_transaction = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    mock_conn.transaction.return_value = mock_transaction
    mock_transaction.start = AsyncMock()

    class FakeRecord(dict):
        pass

    mock_conn.fetch = AsyncMock(return_value=[FakeRecord({"id": 55, "email": "x@x.com"})])

    with patch("core.db_adapter.get_pg_pool", AsyncMock(return_value=mock_pool)):
        async with get_db_connection() as db:
            cursor = await db.execute(
                "INSERT INTO users (email) VALUES (?) RETURNING id, email",
                ("x@x.com",)
            )
            mock_conn.fetch.assert_called_once_with(
                "INSERT INTO users (email) VALUES ($1) RETURNING id, email",
                "x@x.com"
            )
            assert cursor.lastrowid == 55


@pytest.mark.asyncio
async def test_pg_mode_update_and_delete(monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://user:pass@localhost:5432/db")

    mock_pool = MagicMock()
    mock_conn = MagicMock()
    mock_transaction = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    mock_conn.transaction.return_value = mock_transaction
    mock_transaction.start = AsyncMock()
    mock_conn.execute = AsyncMock()

    with patch("core.db_adapter.get_pg_pool", AsyncMock(return_value=mock_pool)):
        async with get_db_connection() as db:
            # UPDATE with params
            await db.execute("UPDATE users SET name = ? WHERE id = ?", ("new_name", 1))
            mock_conn.execute.assert_called_once_with("UPDATE users SET name = $1 WHERE id = $2", "new_name", 1)

            # DELETE without params
            mock_conn.execute.reset_mock()
            await db.execute("DELETE FROM users")
            mock_conn.execute.assert_called_once_with("DELETE FROM users")


@pytest.mark.asyncio
async def test_pg_mode_cursor_aiter(monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://user:pass@localhost:5432/db")

    mock_pool = MagicMock()
    mock_conn = MagicMock()
    mock_transaction = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    mock_conn.transaction.return_value = mock_transaction
    mock_transaction.start = AsyncMock()

    class FakeRecord(dict):
        pass

    mock_conn.fetch = AsyncMock(return_value=[
        FakeRecord({"val": 1}),
        FakeRecord({"val": 2}),
    ])

    with patch("core.db_adapter.get_pg_pool", AsyncMock(return_value=mock_pool)):
        async with get_db_connection() as db:
            cursor = await db.execute("SELECT val FROM numbers")
            results = []
            async for r in cursor:
                results.append(r["val"])
            assert results == [1, 2]


@pytest.mark.asyncio
async def test_pg_mode_transaction_rollback_on_error(monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://user:pass@localhost:5432/db")

    mock_pool = MagicMock()
    mock_conn = MagicMock()
    mock_transaction = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    mock_conn.transaction.return_value = mock_transaction
    mock_transaction.start = AsyncMock()
    mock_transaction.rollback = AsyncMock()
    mock_conn.fetch = AsyncMock(side_effect=RuntimeError("SQL failure"))

    with patch("core.db_adapter.get_pg_pool", AsyncMock(return_value=mock_pool)):
        with pytest.raises(RuntimeError):
            async with get_db_connection() as db:
                await db.execute("SELECT * FROM broken")

        mock_transaction.rollback.assert_called_once()


@pytest.mark.asyncio
async def test_sqlite_adapter_rollback_and_empty_cursor(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        test_db = Path(tmpdir) / "test_rollback.db"
        monkeypatch.setattr(config, "DATABASE_URL", None)
        monkeypatch.setattr(config, "DATABASE_PATH", test_db)

        async with get_db_connection() as db:
            # When no statement has run yet
            assert db.lastrowid is None
            assert await db.fetchone() is None
            assert await db.fetchall() == []

            await db.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, x TEXT)")
            await db.commit()

            await db.execute("INSERT INTO t (x) VALUES (?)", ("hello",))
            await db.rollback()

            cursor = await db.execute("SELECT * FROM t")
            rows = await cursor.fetchall()
            assert len(rows) == 0
