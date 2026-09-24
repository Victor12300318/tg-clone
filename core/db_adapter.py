from __future__ import annotations

import re
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional

import aiosqlite
import asyncpg

from core import config


def convert_query_for_pg(query: str) -> str:
    """
    Replaces positional '?' with '$1, $2, ...' for PostgreSQL queries,
    carefully skipping '?' within single or double quoted SQL string literals
    and comments.
    """
    result = []
    param_idx = 1
    in_quote = None  # "'" or '"'
    in_line_comment = False
    in_block_comment = False
    i = 0
    n = len(query)

    while i < n:
        ch = query[i]

        if in_line_comment:
            result.append(ch)
            if ch == "\n":
                in_line_comment = False
        elif in_block_comment:
            result.append(ch)
            if ch == "*" and i + 1 < n and query[i + 1] == "/":
                result.append("/")
                i += 1
                in_block_comment = False
        elif in_quote:
            result.append(ch)
            if ch == in_quote:
                if i + 1 < n and query[i + 1] == in_quote:
                    result.append(query[i + 1])
                    i += 1
                else:
                    in_quote = None
            elif ch == "\\" and i + 1 < n:
                result.append(query[i + 1])
                i += 1
        else:
            if ch == "-" and i + 1 < n and query[i + 1] == "-":
                in_line_comment = True
                result.append("--")
                i += 1
            elif ch == "/" and i + 1 < n and query[i + 1] == "*":
                in_block_comment = True
                result.append("/*")
                i += 1
            elif ch in ("'", '"'):
                in_quote = ch
                result.append(ch)
            elif ch == "?":
                result.append(f"${param_idx}")
                param_idx += 1
            else:
                result.append(ch)
        i += 1

    return "".join(result)


def is_pg_mode() -> bool:
    """Returns True if PostgreSQL DATABASE_URL is configured, False otherwise."""
    return bool(config.DATABASE_URL)


_pg_pool: Optional[asyncpg.Pool] = None


async def get_pg_pool() -> asyncpg.Pool:
    """Lazily initializes and returns the asyncpg connection pool."""
    global _pg_pool
    if _pg_pool is None:
        _pg_pool = await asyncpg.create_pool(config.DATABASE_URL)
    return _pg_pool


async def close_db_pool() -> None:
    """Closes any active asyncpg connection pool."""
    global _pg_pool
    if _pg_pool is not None:
        await _pg_pool.close()
        _pg_pool = None


class PgCursorAdapter:
    """Cursor-like wrapper over asyncpg fetch results."""

    def __init__(self, rows: list, lastrowid: Any = None):
        self._rows = list(rows) if rows else []
        self._index = 0
        self.lastrowid = lastrowid

    async def fetchone(self) -> Any:
        if self._index < len(self._rows):
            row = self._rows[self._index]
            self._index += 1
            return row
        return None

    async def fetchall(self) -> list:
        if self._index < len(self._rows):
            remaining = self._rows[self._index:]
            self._index = len(self._rows)
            return remaining
        return []

    def __aiter__(self):
        return self

    async def __anext__(self):
        row = await self.fetchone()
        if row is None:
            raise StopAsyncIteration
        return row


class PgConnectionAdapter:
    """Connection wrapper adapting asyncpg.Connection to the common database interface."""

    def __init__(self, conn: asyncpg.Connection):
        self._conn = conn
        self._tr: Optional[asyncpg.transaction.Transaction] = None
        self._last_cursor: Optional[PgCursorAdapter] = None

    async def _ensure_transaction(self) -> None:
        if self._tr is None:
            self._tr = self._conn.transaction()
            await self._tr.start()

    async def execute(self, query: str, params: Any = None) -> PgCursorAdapter:
        if params is None:
            flat_params = ()
        elif isinstance(params, (list, tuple)):
            flat_params = tuple(params)
        else:
            flat_params = (params,)

        converted_query = convert_query_for_pg(query)
        clean_query = converted_query.strip().rstrip(";").strip()

        is_select = bool(re.match(r"^\s*(SELECT|WITH)\b", clean_query, re.IGNORECASE))
        is_insert = bool(re.match(r"^\s*INSERT\b", clean_query, re.IGNORECASE))
        has_returning = bool(re.search(r"\bRETURNING\b", clean_query, re.IGNORECASE))

        if is_insert and not has_returning:
            clean_query = f"{clean_query} RETURNING id"
            has_returning = True

        await self._ensure_transaction()

        lastrowid = None
        rows = []
        try:
            if is_select or has_returning:
                records = await self._conn.fetch(clean_query, *flat_params)
                rows = list(records)
                if has_returning and rows:
                    first = rows[0]
                    if "id" in first:
                        lastrowid = first["id"]
                    elif len(first) > 0:
                        lastrowid = first[0]
            else:
                if flat_params:
                    await self._conn.execute(clean_query, *flat_params)
                else:
                    await self._conn.execute(clean_query)
        except Exception:
            if self._tr is not None:
                try:
                    await self._tr.rollback()
                except Exception:
                    pass
                self._tr = None
            raise

        cursor = PgCursorAdapter(rows, lastrowid=lastrowid)
        self._last_cursor = cursor
        return cursor

    async def commit(self) -> None:
        if self._tr is not None:
            await self._tr.commit()
            self._tr = None

    async def rollback(self) -> None:
        if self._tr is not None:
            await self._tr.rollback()
            self._tr = None

    @property
    def lastrowid(self) -> Optional[int]:
        return self._last_cursor.lastrowid if self._last_cursor else None

    async def fetchone(self) -> Any:
        if self._last_cursor:
            return await self._last_cursor.fetchone()
        return None

    async def fetchall(self) -> list:
        if self._last_cursor:
            return await self._last_cursor.fetchall()
        return []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)


class SqliteConnectionAdapter:
    """Connection wrapper adapting aiosqlite.Connection to the common database interface."""

    def __init__(self, conn: aiosqlite.Connection):
        self._conn = conn
        self._last_cursor: Optional[aiosqlite.Cursor] = None

    async def execute(self, query: str, params: Any = None):
        if params is None:
            cursor = await self._conn.execute(query)
        else:
            cursor = await self._conn.execute(query, params)
        self._last_cursor = cursor
        return cursor

    async def commit(self) -> None:
        await self._conn.commit()

    async def rollback(self) -> None:
        await self._conn.rollback()

    async def close(self) -> None:
        await self._conn.close()

    @property
    def lastrowid(self) -> Optional[int]:
        return self._last_cursor.lastrowid if self._last_cursor else None

    async def fetchone(self) -> Any:
        if self._last_cursor:
            return await self._last_cursor.fetchone()
        return None

    async def fetchall(self) -> list:
        if self._last_cursor:
            return await self._last_cursor.fetchall()
        return []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)


@asynccontextmanager
async def get_db_connection() -> AsyncIterator[SqliteConnectionAdapter | PgConnectionAdapter]:
    """
    Context manager yielding a database connection.
    Uses asyncpg pool when is_pg_mode() is True, or aiosqlite otherwise.
    """
    if not is_pg_mode():
        async with aiosqlite.connect(config.DATABASE_PATH) as db:
            db.row_factory = aiosqlite.Row
            adapter = SqliteConnectionAdapter(db)
            yield adapter
    else:
        pool = await get_pg_pool()
        async with pool.acquire() as conn:
            adapter = PgConnectionAdapter(conn)
            try:
                yield adapter
            except Exception:
                if adapter._tr is not None:
                    try:
                        await adapter._tr.rollback()
                    except Exception:
                        pass
                    adapter._tr = None
                raise
            else:
                if adapter._tr is not None:
                    try:
                        await adapter._tr.rollback()
                    except Exception:
                        pass
                    adapter._tr = None
