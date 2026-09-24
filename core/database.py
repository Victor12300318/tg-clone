from __future__ import annotations
import json
import sqlite3
from datetime import datetime
from typing import List, Optional, Dict, Any

from core.config import DATABASE_PATH
from core.db_adapter import get_db_connection, is_pg_mode, close_db_pool
from core.models import (
    TaskCreate, TaskUpdate, TaskResponse, TaskStatus, TaskMode,
    TextRuleCreate, TextRuleUpdate, TextRuleResponse, LogEntry,
    ManagedGroupCreate, ManagedGroupResponse,
    PostCreate, PostUpdate, PostResponse, PostStatus, ScheduleType,
    PostDeliveryResponse, RecurrenceRule
)


async def init_db() -> None:
    async with get_db_connection() as db:
        if is_pg_mode():
            await db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    subscription_active INTEGER DEFAULT 1,
                    created_at TEXT NOT NULL
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS accounts (
                    id SERIAL PRIMARY KEY,
                    owner_id INTEGER NOT NULL,
                    type TEXT NOT NULL,
                    phone_number TEXT,
                    api_id BIGINT NOT NULL,
                    api_hash TEXT NOT NULL,
                    bot_token TEXT,
                    session_string TEXT,
                    session_name TEXT,
                    tg_user_id BIGINT,
                    username TEXT,
                    first_name TEXT,
                    is_active INTEGER DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            for stmt in (
                "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS owner_id INTEGER",
                "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS tg_user_id BIGINT",
                "ALTER TABLE accounts ALTER COLUMN tg_user_id TYPE BIGINT",
                "ALTER TABLE accounts ALTER COLUMN api_id TYPE BIGINT",
                "UPDATE accounts SET owner_id = 1 WHERE owner_id IS NULL",
            ):
                try:
                    await db.execute(stmt)
                    await db.commit()
                except Exception:
                    pass

            await db.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id SERIAL PRIMARY KEY,
                    owner_id INTEGER NOT NULL DEFAULT 1,
                    name TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    origin_chat TEXT NOT NULL,
                    origin_title TEXT,
                    dest_chat TEXT NOT NULL,
                    dest_title TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    start_message_id BIGINT DEFAULT 1,
                    end_message_id BIGINT,
                    current_message_id BIGINT DEFAULT 0,
                    total_messages BIGINT DEFAULT 0,
                    processed_messages BIGINT DEFAULT 0,
                    copied_count BIGINT DEFAULT 0,
                    skipped_count BIGINT DEFAULT 0,
                    error_count BIGINT DEFAULT 0,
                    media_types_json TEXT NOT NULL DEFAULT '["all"]',
                    clean_forward INTEGER DEFAULT 1,
                    delay_seconds REAL DEFAULT 10.0,
                    skip_delay_seconds REAL DEFAULT 0.5,
                    remove_captions INTEGER DEFAULT 0,
                    remove_links INTEGER DEFAULT 0,
                    remove_mentions INTEGER DEFAULT 0,
                    header_text TEXT DEFAULT '',
                    footer_text TEXT DEFAULT '',
                    custom_replacements_json TEXT DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            for stmt in (
                "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS remove_captions INTEGER DEFAULT 0",
                "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS owner_id INTEGER DEFAULT 1",
                "ALTER TABLE tasks ALTER COLUMN start_message_id TYPE BIGINT",
                "ALTER TABLE tasks ALTER COLUMN end_message_id TYPE BIGINT",
                "ALTER TABLE tasks ALTER COLUMN current_message_id TYPE BIGINT",
                "ALTER TABLE tasks ALTER COLUMN total_messages TYPE BIGINT",
                "ALTER TABLE tasks ALTER COLUMN processed_messages TYPE BIGINT",
                "ALTER TABLE tasks ALTER COLUMN copied_count TYPE BIGINT",
                "ALTER TABLE tasks ALTER COLUMN skipped_count TYPE BIGINT",
                "ALTER TABLE tasks ALTER COLUMN error_count TYPE BIGINT",
            ):
                try:
                    await db.execute(stmt)
                    await db.commit()
                except Exception:
                    pass

            await db.execute("""
                CREATE TABLE IF NOT EXISTS task_messages (
                    id SERIAL PRIMARY KEY,
                    task_id INTEGER NOT NULL,
                    origin_message_id BIGINT NOT NULL,
                    dest_message_id BIGINT,
                    status TEXT NOT NULL,
                    media_type TEXT,
                    copied_at TEXT NOT NULL,
                    UNIQUE(task_id, origin_message_id)
                )
            """)
            for stmt in (
                "ALTER TABLE task_messages ALTER COLUMN origin_message_id TYPE BIGINT",
                "ALTER TABLE task_messages ALTER COLUMN dest_message_id TYPE BIGINT",
            ):
                try:
                    await db.execute(stmt)
                    await db.commit()
                except Exception:
                    pass

            await db.execute("""
                CREATE TABLE IF NOT EXISTS text_rules (
                    id SERIAL PRIMARY KEY,
                    owner_id INTEGER NOT NULL DEFAULT 1,
                    name TEXT NOT NULL,
                    rule_type TEXT NOT NULL DEFAULT 'replace',
                    pattern TEXT NOT NULL,
                    replacement TEXT NOT NULL DEFAULT '',
                    is_regex INTEGER DEFAULT 0,
                    enabled INTEGER DEFAULT 1,
                    created_at TEXT NOT NULL
                )
            """)
            try:
                await db.execute("ALTER TABLE text_rules ADD COLUMN IF NOT EXISTS owner_id INTEGER DEFAULT 1")
                await db.commit()
            except Exception:
                pass

            await db.execute("""
                CREATE TABLE IF NOT EXISTS logs (
                    id SERIAL PRIMARY KEY,
                    owner_id INTEGER,
                    task_id INTEGER,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS system_logs (
                    id SERIAL PRIMARY KEY,
                    owner_id INTEGER,
                    task_id INTEGER,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL,
                    timestamp TEXT,
                    created_at TEXT
                )
            """)
            for stmt in (
                "ALTER TABLE logs ADD COLUMN IF NOT EXISTS owner_id INTEGER",
                "ALTER TABLE system_logs ADD COLUMN IF NOT EXISTS owner_id INTEGER",
            ):
                try:
                    await db.execute(stmt)
                    await db.commit()
                except Exception:
                    pass

            await db.execute("""
                CREATE TABLE IF NOT EXISTS managed_groups (
                    id SERIAL PRIMARY KEY,
                    owner_id INTEGER NOT NULL DEFAULT 1,
                    chat_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    chat_type TEXT NOT NULL DEFAULT 'supergroup',
                    is_admin INTEGER DEFAULT 0,
                    added_at TEXT NOT NULL,
                    UNIQUE(owner_id, chat_id)
                )
            """)
            try:
                await db.execute("ALTER TABLE managed_groups ADD COLUMN IF NOT EXISTS owner_id INTEGER DEFAULT 1")
                await db.commit()
            except Exception:
                pass

            await db.execute("""
                CREATE TABLE IF NOT EXISTS posts (
                    id SERIAL PRIMARY KEY,
                    owner_id INTEGER NOT NULL DEFAULT 1,
                    name TEXT NOT NULL,
                    text TEXT NOT NULL,
                    media_path TEXT,
                    media_type TEXT,
                    target_group_ids_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'draft',
                    schedule_type TEXT NOT NULL DEFAULT 'now',
                    recurrence_rule_json TEXT,
                    run_at TEXT,
                    next_run_at TEXT,
                    last_run_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            try:
                await db.execute("ALTER TABLE posts ADD COLUMN IF NOT EXISTS owner_id INTEGER DEFAULT 1")
                await db.commit()
            except Exception:
                pass

            await db.execute("""
                CREATE TABLE IF NOT EXISTS post_deliveries (
                    id SERIAL PRIMARY KEY,
                    post_id INTEGER NOT NULL,
                    chat_id TEXT NOT NULL,
                    chat_title TEXT,
                    message_id BIGINT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    error TEXT,
                    sent_at TEXT NOT NULL
                )
            """)
            try:
                await db.execute("ALTER TABLE post_deliveries ALTER COLUMN message_id TYPE BIGINT")
                await db.commit()
            except Exception:
                pass
            await db.commit()
        else:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    subscription_active INTEGER DEFAULT 1,
                    created_at TEXT NOT NULL
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_id INTEGER NOT NULL,
                    type TEXT NOT NULL,
                    phone_number TEXT,
                    api_id INTEGER NOT NULL,
                    api_hash TEXT NOT NULL,
                    bot_token TEXT,
                    session_string TEXT,
                    session_name TEXT,
                    tg_user_id INTEGER,
                    username TEXT,
                    first_name TEXT,
                    is_active INTEGER DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            # Migrations for pre-SaaS databases
            for stmt in (
                "ALTER TABLE accounts RENAME COLUMN user_id TO tg_user_id",
                "ALTER TABLE accounts ADD COLUMN owner_id INTEGER",
                "UPDATE accounts SET owner_id = 1 WHERE owner_id IS NULL",
            ):
                try:
                    await db.execute(stmt)
                    await db.commit()
                except Exception:
                    pass

            await db.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_id INTEGER NOT NULL DEFAULT 1,
                    name TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    origin_chat TEXT NOT NULL,
                    origin_title TEXT,
                    dest_chat TEXT NOT NULL,
                    dest_title TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    start_message_id INTEGER DEFAULT 1,
                    end_message_id INTEGER,
                    current_message_id INTEGER DEFAULT 0,
                    total_messages INTEGER DEFAULT 0,
                    processed_messages INTEGER DEFAULT 0,
                    copied_count INTEGER DEFAULT 0,
                    skipped_count INTEGER DEFAULT 0,
                    error_count INTEGER DEFAULT 0,
                    media_types_json TEXT NOT NULL DEFAULT '["all"]',
                    clean_forward INTEGER DEFAULT 1,
                    delay_seconds REAL DEFAULT 10.0,
                    skip_delay_seconds REAL DEFAULT 0.5,
                    remove_captions INTEGER DEFAULT 0,
                    remove_links INTEGER DEFAULT 0,
                    remove_mentions INTEGER DEFAULT 0,
                    header_text TEXT DEFAULT '',
                    footer_text TEXT DEFAULT '',
                    custom_replacements_json TEXT DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

            try:
                await db.execute("ALTER TABLE tasks ADD COLUMN remove_captions INTEGER DEFAULT 0")
                await db.commit()
            except Exception:
                pass
            try:
                await db.execute("ALTER TABLE tasks ADD COLUMN owner_id INTEGER DEFAULT 1")
                await db.commit()
            except Exception:
                pass

            await db.execute("""
                CREATE TABLE IF NOT EXISTS task_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL,
                    origin_message_id INTEGER NOT NULL,
                    dest_message_id INTEGER,
                    status TEXT NOT NULL,
                    media_type TEXT,
                    copied_at TEXT NOT NULL,
                    UNIQUE(task_id, origin_message_id)
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS text_rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_id INTEGER NOT NULL DEFAULT 1,
                    name TEXT NOT NULL,
                    rule_type TEXT NOT NULL DEFAULT 'replace',
                    pattern TEXT NOT NULL,
                    replacement TEXT NOT NULL DEFAULT '',
                    is_regex INTEGER DEFAULT 0,
                    enabled INTEGER DEFAULT 1,
                    created_at TEXT NOT NULL
                )
            """)
            try:
                await db.execute("ALTER TABLE text_rules ADD COLUMN owner_id INTEGER DEFAULT 1")
                await db.commit()
            except Exception:
                pass

            await db.execute("""
                CREATE TABLE IF NOT EXISTS logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_id INTEGER,
                    task_id INTEGER,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS system_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_id INTEGER,
                    task_id INTEGER,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL,
                    timestamp TEXT,
                    created_at TEXT
                )
            """)
            try:
                await db.execute("ALTER TABLE logs ADD COLUMN owner_id INTEGER")
                await db.commit()
            except Exception:
                pass

            # ponytail: legacy single-tenant DBs keep UNIQUE(chat_id); fresh SaaS DBs
            # scope uniqueness per owner. Recreate-table migration when legacy data matters.
            await db.execute("""
                CREATE TABLE IF NOT EXISTS managed_groups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_id INTEGER NOT NULL DEFAULT 1,
                    chat_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    chat_type TEXT NOT NULL DEFAULT 'supergroup',
                    is_admin INTEGER DEFAULT 0,
                    added_at TEXT NOT NULL,
                    UNIQUE(owner_id, chat_id)
                )
            """)
            try:
                await db.execute("ALTER TABLE managed_groups ADD COLUMN owner_id INTEGER DEFAULT 1")
                await db.commit()
            except Exception:
                pass

            await db.execute("""
                CREATE TABLE IF NOT EXISTS posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_id INTEGER NOT NULL DEFAULT 1,
                    name TEXT NOT NULL,
                    text TEXT NOT NULL,
                    media_path TEXT,
                    media_type TEXT,
                    target_group_ids_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'draft',
                    schedule_type TEXT NOT NULL DEFAULT 'now',
                    recurrence_rule_json TEXT,
                    run_at TEXT,
                    next_run_at TEXT,
                    last_run_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            try:
                await db.execute("ALTER TABLE posts ADD COLUMN owner_id INTEGER DEFAULT 1")
                await db.commit()
            except Exception:
                pass

            await db.execute("""
                CREATE TABLE IF NOT EXISTS post_deliveries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    post_id INTEGER NOT NULL,
                    chat_id TEXT NOT NULL,
                    chat_title TEXT,
                    message_id INTEGER,
                    status TEXT NOT NULL DEFAULT 'pending',
                    error TEXT,
                    sent_at TEXT NOT NULL
                )
            """)
            await db.commit()

    # Seed or synchronize default admin user configured in .env
    await ensure_default_user()


# User DB Operations
async def ensure_default_user() -> None:
    from core import config
    from core.security import hash_password
    now = datetime.now().isoformat()
    default_email = config.DEFAULT_USER_EMAIL
    default_pass = config.DEFAULT_USER_PASSWORD

    async with get_db_connection() as db:
        cursor = await db.execute(
            "SELECT id, email, password_hash FROM users WHERE email = ? OR id = 1",
            (default_email,)
        )
        user = await cursor.fetchone()
        if not user:
            await db.execute(
                "INSERT INTO users (email, password_hash, subscription_active, created_at) VALUES (?, ?, 1, ?)",
                (default_email, hash_password(default_pass), now)
            )
            await db.commit()
        else:
            user_id = user["id"]
            await db.execute(
                "UPDATE users SET email = ?, subscription_active = 1 WHERE id = ?",
                (default_email, user_id)
            )
            await db.commit()


async def update_user_password(user_id: int, password_hash: str) -> None:
    async with get_db_connection() as db:
        await db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))
        await db.commit()
async def create_user(email: str, password_hash: str) -> Optional[int]:
    now = datetime.now().isoformat()
    async with get_db_connection() as db:
        try:
            cursor = await db.execute(
                "INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, ?)",
                (email.lower().strip(), password_hash, now)
            )
            await db.commit()
            return cursor.lastrowid
        except (sqlite3.IntegrityError, Exception) as e:
            if isinstance(e, sqlite3.IntegrityError) or "unique" in str(e).lower() or "duplicate" in str(e).lower():
                return None
            raise


async def get_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    async with get_db_connection() as db:
        cursor = await db.execute("SELECT * FROM users WHERE email = ?", (email.lower().strip(),))
        row = await cursor.fetchone()
        if row:
            return dict(row)
        return None


async def get_user_by_id(user_id: int) -> Optional[Dict[str, Any]]:
    async with get_db_connection() as db:
        cursor = await db.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        row = await cursor.fetchone()
        if row:
            return dict(row)
        return None


# Account DB Operations
async def save_account(
    owner_id: int,
    account_type: str,
    api_id: int,
    api_hash: str,
    phone_number: Optional[str] = None,
    bot_token: Optional[str] = None,
    session_string: Optional[str] = None,
    session_name: Optional[str] = None,
    tg_user_id: Optional[int] = None,
    username: Optional[str] = None,
    first_name: Optional[str] = None
) -> int:
    now = datetime.now().isoformat()
    async with get_db_connection() as db:
        # Exactly one active account per owner
        await db.execute("UPDATE accounts SET is_active = 0 WHERE owner_id = ?", (owner_id,))
        cursor = await db.execute("""
            INSERT INTO accounts (
                owner_id, type, phone_number, api_id, api_hash, bot_token,
                session_string, session_name, tg_user_id, username, first_name,
                is_active, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        """, (
            owner_id, account_type, phone_number, api_id, api_hash, bot_token,
            session_string, session_name, tg_user_id, username, first_name,
            now, now
        ))
        await db.commit()
        return cursor.lastrowid


async def get_active_account(owner_id: int) -> Optional[Dict[str, Any]]:
    async with get_db_connection() as db:
        cursor = await db.execute(
            "SELECT * FROM accounts WHERE owner_id = ? AND is_active = 1 ORDER BY id DESC LIMIT 1",
            (owner_id,)
        )
        row = await cursor.fetchone()
        if row:
            return dict(row)
        return None


async def get_all_accounts(owner_id: int) -> List[Dict[str, Any]]:
    async with get_db_connection() as db:
        cursor = await db.execute(
            "SELECT * FROM accounts WHERE owner_id = ? ORDER BY id DESC", (owner_id,)
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def clear_active_account(owner_id: int) -> None:
    async with get_db_connection() as db:
        await db.execute("UPDATE accounts SET is_active = 0 WHERE owner_id = ?", (owner_id,))
        await db.commit()


# Task DB Operations
def _row_to_task_response(row: Any) -> TaskResponse:
    d = dict(row)
    media_types = json.loads(d.get("media_types_json") or '["all"]')
    custom_replacements = json.loads(d.get("custom_replacements_json") or '[]')
    return TaskResponse(
        id=d["id"],
        owner_id=d.get("owner_id"),
        name=d["name"],
        mode=d["mode"],
        origin_chat=d["origin_chat"],
        origin_title=d.get("origin_title"),
        dest_chat=d["dest_chat"],
        dest_title=d.get("dest_title"),
        status=d["status"],
        start_message_id=d.get("start_message_id", 1),
        end_message_id=d.get("end_message_id"),
        current_message_id=d.get("current_message_id", 0),
        total_messages=d.get("total_messages", 0),
        processed_messages=d.get("processed_messages", 0),
        copied_count=d.get("copied_count", 0),
        skipped_count=d.get("skipped_count", 0),
        error_count=d.get("error_count", 0),
        media_types=media_types,
        clean_forward=bool(d.get("clean_forward", 1)),
        delay_seconds=float(d.get("delay_seconds", 10.0)),
        skip_delay_seconds=float(d.get("skip_delay_seconds", 0.5)),
        remove_captions=bool(d.get("remove_captions", 0)),
        remove_links=bool(d.get("remove_links", 0)),
        remove_mentions=bool(d.get("remove_mentions", 0)),
        header_text=d.get("header_text", ""),
        footer_text=d.get("footer_text", ""),
        custom_replacements=custom_replacements,
        created_at=d["created_at"],
        updated_at=d["updated_at"]
    )


async def create_task(owner_id: int, task_in: TaskCreate, origin_title: Optional[str] = None, dest_title: Optional[str] = None) -> TaskResponse:
    now = datetime.now().isoformat()
    media_types_json = json.dumps(task_in.media_types)
    custom_replacements_json = json.dumps(task_in.custom_replacements or [])
    delay = task_in.delay_seconds if task_in.delay_seconds is not None else 10.0
    skip_delay = task_in.skip_delay_seconds if task_in.skip_delay_seconds is not None else 0.5

    async with get_db_connection() as db:
        cursor = await db.execute("""
            INSERT INTO tasks (
                owner_id, name, mode, origin_chat, origin_title, dest_chat, dest_title,
                status, start_message_id, end_message_id, current_message_id,
                total_messages, processed_messages, copied_count, skipped_count, error_count,
                media_types_json, clean_forward, delay_seconds, skip_delay_seconds,
                remove_captions, remove_links, remove_mentions, header_text, footer_text,
                custom_replacements_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            owner_id,
            task_in.name, task_in.mode.value if hasattr(task_in.mode, 'value') else task_in.mode,
            task_in.origin_chat, origin_title, task_in.dest_chat, dest_title,
            TaskStatus.PENDING.value, task_in.start_message_id or 1, task_in.end_message_id, 0,
            0, 0, 0, 0, 0,
            media_types_json, 1 if task_in.clean_forward else 0, delay, skip_delay,
            1 if task_in.remove_captions else 0, 1 if task_in.remove_links else 0, 1 if task_in.remove_mentions else 0,
            task_in.header_text or "", task_in.footer_text or "",
            custom_replacements_json, now, now
        ))
        task_id = cursor.lastrowid
        await db.commit()

        cursor = await db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        row = await cursor.fetchone()
        return _row_to_task_response(row)


async def get_task(owner_id: int, task_id: int) -> Optional[TaskResponse]:
    async with get_db_connection() as db:
        cursor = await db.execute("SELECT * FROM tasks WHERE id = ? AND owner_id = ?", (task_id, owner_id))
        row = await cursor.fetchone()
        if row:
            return _row_to_task_response(row)
        return None


async def get_all_tasks(owner_id: int) -> List[TaskResponse]:
    async with get_db_connection() as db:
        cursor = await db.execute("SELECT * FROM tasks WHERE owner_id = ? ORDER BY id DESC", (owner_id,))
        rows = await cursor.fetchall()
        return [_row_to_task_response(r) for r in rows]


async def get_live_tasks_all() -> List[TaskResponse]:
    """Tarefas live de todos os donos — usado apenas pelo re-arme no startup."""
    async with get_db_connection() as db:
        cursor = await db.execute(
            "SELECT * FROM tasks WHERE mode = ? AND status = ?",
            (TaskMode.LIVE_SYNC.value, TaskStatus.RUNNING.value)
        )
        rows = await cursor.fetchall()
        return [_row_to_task_response(r) for r in rows]


async def update_task(
    owner_id: int,
    task_id: int,
    task_in: TaskUpdate,
    origin_title: Optional[str] = None,
    dest_title: Optional[str] = None
) -> Optional[TaskResponse]:
    now = datetime.now().isoformat()
    fields: List[str] = ["updated_at = ?"]
    values: List[Any] = [now]

    if task_in.name is not None:
        fields.append("name = ?")
        values.append(task_in.name)
    if task_in.mode is not None:
        fields.append("mode = ?")
        values.append(task_in.mode.value if hasattr(task_in.mode, 'value') else task_in.mode)
    if task_in.origin_chat is not None:
        fields.append("origin_chat = ?")
        values.append(task_in.origin_chat)
    if origin_title is not None:
        fields.append("origin_title = ?")
        values.append(origin_title)
    if task_in.dest_chat is not None:
        fields.append("dest_chat = ?")
        values.append(task_in.dest_chat)
    if dest_title is not None:
        fields.append("dest_title = ?")
        values.append(dest_title)
    if task_in.start_message_id is not None:
        fields.append("start_message_id = ?")
        values.append(task_in.start_message_id)
    if task_in.end_message_id is not None:
        fields.append("end_message_id = ?")
        values.append(task_in.end_message_id)
    if task_in.media_types is not None:
        fields.append("media_types_json = ?")
        values.append(json.dumps(task_in.media_types))
    if task_in.clean_forward is not None:
        fields.append("clean_forward = ?")
        values.append(1 if task_in.clean_forward else 0)
    if task_in.delay_seconds is not None:
        fields.append("delay_seconds = ?")
        values.append(task_in.delay_seconds)
    if task_in.skip_delay_seconds is not None:
        fields.append("skip_delay_seconds = ?")
        values.append(task_in.skip_delay_seconds)
    if task_in.remove_captions is not None:
        fields.append("remove_captions = ?")
        values.append(1 if task_in.remove_captions else 0)
    if task_in.remove_links is not None:
        fields.append("remove_links = ?")
        values.append(1 if task_in.remove_links else 0)
    if task_in.remove_mentions is not None:
        fields.append("remove_mentions = ?")
        values.append(1 if task_in.remove_mentions else 0)
    if task_in.header_text is not None:
        fields.append("header_text = ?")
        values.append(task_in.header_text)
    if task_in.footer_text is not None:
        fields.append("footer_text = ?")
        values.append(task_in.footer_text)
    if task_in.custom_replacements is not None:
        fields.append("custom_replacements_json = ?")
        values.append(json.dumps(task_in.custom_replacements))

    values.extend([task_id, owner_id])
    sql = f"UPDATE tasks SET {', '.join(fields)} WHERE id = ? AND owner_id = ?"
    async with get_db_connection() as db:
        cursor = await db.execute(sql, tuple(values))
        await db.commit()
        if cursor.rowcount == 0:
            return None
        return await get_task(owner_id, task_id)


async def update_task_status(owner_id: int, task_id: int, status: TaskStatus) -> None:
    now = datetime.now().isoformat()
    async with get_db_connection() as db:
        await db.execute("""
            UPDATE tasks SET status = ?, updated_at = ? WHERE id = ? AND owner_id = ?
        """, (status.value if hasattr(status, 'value') else status, now, task_id, owner_id))
        await db.commit()


async def update_task_progress(
    owner_id: int,
    task_id: int,
    current_message_id: Optional[int] = None,
    total_messages: Optional[int] = None,
    processed_messages: Optional[int] = None,
    copied_count: Optional[int] = None,
    skipped_count: Optional[int] = None,
    error_count: Optional[int] = None
) -> None:
    now = datetime.now().isoformat()
    fields = ["updated_at = ?"]
    values: List[Any] = [now]

    if current_message_id is not None:
        fields.append("current_message_id = ?")
        values.append(current_message_id)
    if total_messages is not None:
        fields.append("total_messages = ?")
        values.append(total_messages)
    if processed_messages is not None:
        fields.append("processed_messages = ?")
        values.append(processed_messages)
    if copied_count is not None:
        fields.append("copied_count = ?")
        values.append(copied_count)
    if skipped_count is not None:
        fields.append("skipped_count = ?")
        values.append(skipped_count)
    if error_count is not None:
        fields.append("error_count = ?")
        values.append(error_count)

    values.extend([task_id, owner_id])
    sql = f"UPDATE tasks SET {', '.join(fields)} WHERE id = ? AND owner_id = ?"
    async with get_db_connection() as db:
        await db.execute(sql, tuple(values))
        await db.commit()


async def delete_task(owner_id: int, task_id: int) -> bool:
    async with get_db_connection() as db:
        cursor = await db.execute("SELECT id FROM tasks WHERE id = ? AND owner_id = ?", (task_id, owner_id))
        if not await cursor.fetchone():
            return False
        await db.execute("DELETE FROM task_messages WHERE task_id = ?", (task_id,))
        await db.execute("DELETE FROM logs WHERE task_id = ?", (task_id,))
        await db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        await db.commit()
        return True


# Task Message Cache DB Operations
async def record_task_message(
    task_id: int,
    origin_message_id: int,
    status: str,
    dest_message_id: Optional[int] = None,
    media_type: Optional[str] = None
) -> None:
    now = datetime.now().isoformat()
    async with get_db_connection() as db:
        if is_pg_mode():
            await db.execute("""
                INSERT INTO task_messages (
                    task_id, origin_message_id, dest_message_id, status, media_type, copied_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (task_id, origin_message_id) DO UPDATE SET
                    dest_message_id = EXCLUDED.dest_message_id,
                    status = EXCLUDED.status,
                    media_type = EXCLUDED.media_type,
                    copied_at = EXCLUDED.copied_at
            """, (task_id, origin_message_id, dest_message_id, status, media_type, now))
        else:
            await db.execute("""
                INSERT OR REPLACE INTO task_messages (
                    task_id, origin_message_id, dest_message_id, status, media_type, copied_at
                ) VALUES (?, ?, ?, ?, ?, ?)
            """, (task_id, origin_message_id, dest_message_id, status, media_type, now))
        await db.commit()


async def get_task_copied_message_ids(task_id: int) -> List[int]:
    async with get_db_connection() as db:
        cursor = await db.execute(
            "SELECT origin_message_id FROM task_messages WHERE task_id = ? ORDER BY origin_message_id ASC",
            (task_id,)
        )
        rows = await cursor.fetchall()
        return [r["origin_message_id"] for r in rows]


# Text Rules DB Operations
async def create_text_rule(owner_id: int, rule_in: TextRuleCreate) -> TextRuleResponse:
    now = datetime.now().isoformat()
    async with get_db_connection() as db:
        cursor = await db.execute("""
            INSERT INTO text_rules (
                owner_id, name, rule_type, pattern, replacement, is_regex, enabled, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            owner_id, rule_in.name, rule_in.rule_type, rule_in.pattern, rule_in.replacement,
            1 if rule_in.is_regex else 0, 1 if rule_in.enabled else 0, now
        ))
        rule_id = cursor.lastrowid
        await db.commit()
        return TextRuleResponse(
            id=rule_id,
            name=rule_in.name,
            rule_type=rule_in.rule_type,
            pattern=rule_in.pattern,
            replacement=rule_in.replacement,
            is_regex=rule_in.is_regex,
            enabled=rule_in.enabled,
            created_at=now
        )


async def get_all_text_rules(owner_id: int) -> List[TextRuleResponse]:
    async with get_db_connection() as db:
        cursor = await db.execute("SELECT * FROM text_rules WHERE owner_id = ? ORDER BY id DESC", (owner_id,))
        rows = await cursor.fetchall()
        return [
            TextRuleResponse(
                id=r["id"],
                name=r["name"],
                rule_type=r["rule_type"],
                pattern=r["pattern"],
                replacement=r["replacement"],
                is_regex=bool(r["is_regex"]),
                enabled=bool(r["enabled"]),
                created_at=r["created_at"]
            )
            for r in rows
        ]


async def update_text_rule(owner_id: int, rule_id: int, rule_in: TextRuleUpdate) -> Optional[TextRuleResponse]:
    fields: List[str] = []
    values: List[Any] = []

    if rule_in.name is not None:
        fields.append("name = ?")
        values.append(rule_in.name)
    if rule_in.rule_type is not None:
        fields.append("rule_type = ?")
        values.append(rule_in.rule_type)
    if rule_in.pattern is not None:
        fields.append("pattern = ?")
        values.append(rule_in.pattern)
    if rule_in.replacement is not None:
        fields.append("replacement = ?")
        values.append(rule_in.replacement)
    if rule_in.is_regex is not None:
        fields.append("is_regex = ?")
        values.append(1 if rule_in.is_regex else 0)
    if rule_in.enabled is not None:
        fields.append("enabled = ?")
        values.append(1 if rule_in.enabled else 0)

    if not fields:
        async with get_db_connection() as db:
            cursor = await db.execute("SELECT * FROM text_rules WHERE id = ? AND owner_id = ?", (rule_id, owner_id))
            row = await cursor.fetchone()
            if not row:
                return None
            return TextRuleResponse(
                id=row["id"], name=row["name"], rule_type=row["rule_type"],
                pattern=row["pattern"], replacement=row["replacement"],
                is_regex=bool(row["is_regex"]), enabled=bool(row["enabled"]),
                created_at=row["created_at"]
            )

    values.extend([rule_id, owner_id])
    sql = f"UPDATE text_rules SET {', '.join(fields)} WHERE id = ? AND owner_id = ?"
    async with get_db_connection() as db:
        cursor = await db.execute(sql, tuple(values))
        await db.commit()
        if cursor.rowcount == 0:
            return None
        cursor = await db.execute("SELECT * FROM text_rules WHERE id = ? AND owner_id = ?", (rule_id, owner_id))
        row = await cursor.fetchone()
        return TextRuleResponse(
            id=row["id"], name=row["name"], rule_type=row["rule_type"],
            pattern=row["pattern"], replacement=row["replacement"],
            is_regex=bool(row["is_regex"]), enabled=bool(row["enabled"]),
            created_at=row["created_at"]
        )


async def delete_text_rule(owner_id: int, rule_id: int) -> bool:
    async with get_db_connection() as db:
        cursor = await db.execute("DELETE FROM text_rules WHERE id = ? AND owner_id = ?", (rule_id, owner_id))
        await db.commit()
        return cursor.rowcount > 0


# Logs DB Operations
async def add_log(owner_id: Optional[int], task_id: Optional[int], level: str, message: str) -> None:
    now = datetime.now().strftime("%H:%M:%S")
    async with get_db_connection() as db:
        await db.execute("""
            INSERT INTO logs (owner_id, task_id, level, message, timestamp)
            VALUES (?, ?, ?, ?, ?)
        """, (owner_id, task_id, level, message, now))
        await db.commit()


async def get_recent_logs(owner_id: int, limit: int = 100, task_id: Optional[int] = None) -> List[LogEntry]:
    async with get_db_connection() as db:
        if task_id is not None:
            cursor = await db.execute(
                "SELECT * FROM logs WHERE owner_id = ? AND task_id = ? ORDER BY id DESC LIMIT ?",
                (owner_id, task_id, limit)
            )
        else:
            cursor = await db.execute(
                "SELECT * FROM logs WHERE owner_id = ? ORDER BY id DESC LIMIT ?",
                (owner_id, limit)
            )
        rows = await cursor.fetchall()
        return [
            LogEntry(
                task_id=r["task_id"],
                level=r["level"],
                message=r["message"],
                timestamp=r["timestamp"]
            )
            for r in reversed(rows)
        ]


# Managed Groups Operations
async def add_managed_group(owner_id: int, group_in: ManagedGroupCreate) -> ManagedGroupResponse:
    now = datetime.now().isoformat()
    async with get_db_connection() as db:
        cursor = await db.execute("""
            INSERT INTO managed_groups (owner_id, chat_id, title, chat_type, is_admin, added_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(owner_id, chat_id) DO UPDATE SET
                title = excluded.title,
                chat_type = excluded.chat_type,
                is_admin = excluded.is_admin
        """, (
            owner_id, group_in.chat_id, group_in.title, group_in.chat_type,
            1 if group_in.is_admin else 0, now
        ))
        await db.commit()
        cursor = await db.execute(
            "SELECT * FROM managed_groups WHERE owner_id = ? AND chat_id = ?",
            (owner_id, group_in.chat_id)
        )
        row = await cursor.fetchone()
        return ManagedGroupResponse(
            id=row["id"], chat_id=row["chat_id"], title=row["title"],
            chat_type=row["chat_type"], is_admin=bool(row["is_admin"]), added_at=row["added_at"]
        )


async def add_managed_groups_bulk(owner_id: int, groups_in: List[ManagedGroupCreate]) -> List[ManagedGroupResponse]:
    results = []
    for g in groups_in:
        results.append(await add_managed_group(owner_id, g))
    return results


async def get_all_managed_groups(owner_id: Optional[int] = None) -> List[ManagedGroupResponse]:
    async with get_db_connection() as db:
        if owner_id is not None:
            cursor = await db.execute("SELECT * FROM managed_groups WHERE owner_id = ? ORDER BY id DESC", (owner_id,))
        else:
            cursor = await db.execute("SELECT * FROM managed_groups ORDER BY id DESC")
        rows = await cursor.fetchall()
        return [
            ManagedGroupResponse(
                id=r["id"], chat_id=r["chat_id"], title=r["title"],
                chat_type=r["chat_type"], is_admin=bool(r["is_admin"]), added_at=r["added_at"]
            )
            for r in rows
        ]


async def get_managed_group_by_id(owner_id: int, group_id: int) -> Optional[ManagedGroupResponse]:
    async with get_db_connection() as db:
        cursor = await db.execute("SELECT * FROM managed_groups WHERE id = ? AND owner_id = ?", (group_id, owner_id))
        row = await cursor.fetchone()
        if not row:
            return None
        return ManagedGroupResponse(
            id=row["id"], chat_id=row["chat_id"], title=row["title"],
            chat_type=row["chat_type"], is_admin=bool(row["is_admin"]), added_at=row["added_at"]
        )


async def delete_managed_group(owner_id: int, group_id: int) -> bool:
    async with get_db_connection() as db:
        cursor = await db.execute("DELETE FROM managed_groups WHERE id = ? AND owner_id = ?", (group_id, owner_id))
        await db.commit()
        return cursor.rowcount > 0


# Posts DB Operations
def _row_to_post_response(row: Any, total_deliv: int = 0, success_deliv: int = 0) -> PostResponse:
    d = dict(row)
    target_group_ids = json.loads(d.get("target_group_ids_json") or "[]")
    rec_rule_json = d.get("recurrence_rule_json")
    rec_rule = RecurrenceRule(**json.loads(rec_rule_json)) if rec_rule_json else None
    return PostResponse(
        id=d["id"],
        owner_id=d.get("owner_id"),
        name=d["name"],
        text=d["text"],
        media_path=d.get("media_path"),
        media_type=d.get("media_type"),
        target_group_ids=target_group_ids,
        status=d["status"],
        schedule_type=d["schedule_type"],
        recurrence_rule=rec_rule,
        run_at=d.get("run_at"),
        next_run_at=d.get("next_run_at"),
        last_run_at=d.get("last_run_at"),
        created_at=d["created_at"],
        updated_at=d["updated_at"],
        total_deliveries=total_deliv,
        successful_deliveries=success_deliv
    )


async def create_post(owner_id: int, post_in: PostCreate) -> PostResponse:
    now = datetime.now().isoformat()
    target_ids_json = json.dumps(post_in.target_group_ids or [])
    rec_rule_json = json.dumps(post_in.recurrence_rule.dict()) if post_in.recurrence_rule else None

    # Calculate initial next_run_at
    next_run = post_in.run_at if post_in.schedule_type == ScheduleType.ONCE else (
        post_in.run_at or now if post_in.schedule_type == ScheduleType.RECURRING else None
    )

    async with get_db_connection() as db:
        cursor = await db.execute("""
            INSERT INTO posts (
                owner_id, name, text, media_path, media_type, target_group_ids_json,
                status, schedule_type, recurrence_rule_json, run_at, next_run_at,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            owner_id, post_in.name, post_in.text, post_in.media_path, post_in.media_type,
            target_ids_json, PostStatus.DRAFT.value,
            post_in.schedule_type.value if hasattr(post_in.schedule_type, 'value') else post_in.schedule_type,
            rec_rule_json, post_in.run_at, next_run, now, now
        ))
        post_id = cursor.lastrowid
        await db.commit()
        return await get_post(owner_id, post_id)


async def get_post(owner_id: int, post_id: int) -> Optional[PostResponse]:
    async with get_db_connection() as db:
        cursor = await db.execute("SELECT * FROM posts WHERE id = ? AND owner_id = ?", (post_id, owner_id))
        row = await cursor.fetchone()
        if not row:
            return None
        
        # Count deliveries
        cursor = await db.execute("SELECT COUNT(*), SUM(CASE WHEN status IN ('sent', 'edited') THEN 1 ELSE 0 END) FROM post_deliveries WHERE post_id = ?", (post_id,))
        deliv_row = await cursor.fetchone()
        total_deliv = deliv_row[0] or 0
        success_deliv = deliv_row[1] or 0

        return _row_to_post_response(row, total_deliv, success_deliv)


async def get_all_posts(owner_id: Optional[int] = None) -> List[PostResponse]:
    """owner_id None = varredura do scheduler (todos os usuários); inteiro = listagem do dono."""
    async with get_db_connection() as db:
        if owner_id is not None:
            cursor = await db.execute("SELECT * FROM posts WHERE owner_id = ? ORDER BY id DESC", (owner_id,))
        else:
            cursor = await db.execute("SELECT * FROM posts ORDER BY id DESC")
        rows = await cursor.fetchall()
        result = []
        for r in rows:
            cursor = await db.execute("SELECT COUNT(*), SUM(CASE WHEN status IN ('sent', 'edited') THEN 1 ELSE 0 END) FROM post_deliveries WHERE post_id = ?", (r["id"],))
            deliv_row = await cursor.fetchone()
            total_deliv = deliv_row[0] or 0
            success_deliv = deliv_row[1] or 0
            result.append(_row_to_post_response(r, total_deliv, success_deliv))
        return result


async def update_post_status(post_id: int, status: PostStatus) -> None:
    now = datetime.now().isoformat()
    async with get_db_connection() as db:
        await db.execute("""
            UPDATE posts SET status = ?, updated_at = ? WHERE id = ?
        """, (status.value if hasattr(status, 'value') else status, now, post_id))
        await db.commit()


async def update_post_next_run(post_id: int, next_run_at: Optional[str], last_run_at: Optional[str] = None) -> None:
    now = datetime.now().isoformat()
    fields = ["next_run_at = ?", "updated_at = ?"]
    values = [next_run_at, now]
    if last_run_at is not None:
        fields.append("last_run_at = ?")
        values.append(last_run_at)
    values.append(post_id)
    async with get_db_connection() as db:
        await db.execute(f"UPDATE posts SET {', '.join(fields)} WHERE id = ?", tuple(values))
        await db.commit()


async def update_post_content(owner_id: int, post_id: int, post_in: PostUpdate) -> Optional[PostResponse]:
    now = datetime.now().isoformat()
    fields: List[str] = ["updated_at = ?"]
    values: List[Any] = [now]

    if post_in.name is not None:
        fields.append("name = ?")
        values.append(post_in.name)
    if post_in.text is not None:
        fields.append("text = ?")
        values.append(post_in.text)
    if post_in.media_path is not None:
        fields.append("media_path = ?")
        values.append(post_in.media_path)
    if post_in.media_type is not None:
        fields.append("media_type = ?")
        values.append(post_in.media_type)
    if post_in.target_group_ids is not None:
        fields.append("target_group_ids_json = ?")
        values.append(json.dumps(post_in.target_group_ids))
    if post_in.schedule_type is not None:
        fields.append("schedule_type = ?")
        values.append(post_in.schedule_type.value if hasattr(post_in.schedule_type, 'value') else post_in.schedule_type)
    if post_in.recurrence_rule is not None:
        fields.append("recurrence_rule_json = ?")
        values.append(json.dumps(post_in.recurrence_rule.dict()))
    if post_in.run_at is not None:
        fields.append("run_at = ?")
        values.append(post_in.run_at)
    if post_in.status is not None:
        fields.append("status = ?")
        values.append(post_in.status.value if hasattr(post_in.status, 'value') else post_in.status)

    values.extend([post_id, owner_id])
    async with get_db_connection() as db:
        await db.execute(f"UPDATE posts SET {', '.join(fields)} WHERE id = ? AND owner_id = ?", tuple(values))
        await db.commit()
        return await get_post(owner_id, post_id)


async def delete_post(owner_id: int, post_id: int) -> bool:
    async with get_db_connection() as db:
        cursor = await db.execute("SELECT id FROM posts WHERE id = ? AND owner_id = ?", (post_id, owner_id))
        if not await cursor.fetchone():
            return False
        await db.execute("DELETE FROM post_deliveries WHERE post_id = ?", (post_id,))
        await db.execute("DELETE FROM posts WHERE id = ?", (post_id,))
        await db.commit()
        return True


# Post Deliveries Operations
async def record_post_delivery(
    post_id: int,
    chat_id: str,
    chat_title: Optional[str] = None,
    message_id: Optional[int] = None,
    status: str = "sent",
    error: Optional[str] = None
) -> PostDeliveryResponse:
    now = datetime.now().isoformat()
    async with get_db_connection() as db:
        cursor = await db.execute("""
            INSERT INTO post_deliveries (post_id, chat_id, chat_title, message_id, status, error, sent_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (post_id, chat_id, chat_title, message_id, status, error, now))
        deliv_id = cursor.lastrowid
        await db.commit()
        return PostDeliveryResponse(
            id=deliv_id, post_id=post_id, chat_id=chat_id, chat_title=chat_title,
            message_id=message_id, status=status, error=error, sent_at=now
        )


async def get_post_deliveries(owner_id: int, post_id: int) -> List[PostDeliveryResponse]:
    async with get_db_connection() as db:
        cursor = await db.execute(
            "SELECT * FROM post_deliveries WHERE post_id = ? AND post_id IN (SELECT id FROM posts WHERE id = ? AND owner_id = ?) ORDER BY id ASC",
            (post_id, post_id, owner_id)
        )
        rows = await cursor.fetchall()
        return [
            PostDeliveryResponse(
                id=r["id"], post_id=r["post_id"], chat_id=r["chat_id"], chat_title=r["chat_title"],
                message_id=r["message_id"], status=r["status"], error=r["error"], sent_at=r["sent_at"]
            )
            for r in rows
        ]


async def get_latest_deliveries_for_post(post_id: int) -> List[PostDeliveryResponse]:
    """Returns the most recent delivery record per chat_id for a given post."""
    async with get_db_connection() as db:
        cursor = await db.execute("""
            SELECT * FROM post_deliveries
            WHERE id IN (
                SELECT MAX(id) FROM post_deliveries WHERE post_id = ? GROUP BY chat_id
            )
        """, (post_id,))
        rows = await cursor.fetchall()
        return [
            PostDeliveryResponse(
                id=r["id"], post_id=r["post_id"], chat_id=r["chat_id"], chat_title=r["chat_title"],
                message_id=r["message_id"], status=r["status"], error=r["error"], sent_at=r["sent_at"]
            )
            for r in rows
        ]


async def update_delivery_status(delivery_id: int, status: str, error: Optional[str] = None) -> None:
    async with get_db_connection() as db:
        await db.execute("UPDATE post_deliveries SET status = ?, error = ? WHERE id = ?", (status, error, delivery_id))
        await db.commit()


