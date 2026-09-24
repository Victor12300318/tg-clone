#!/usr/bin/env python3
"""
Script de migração de dados do SQLite para PostgreSQL.
Transfere registros existentes preservando integridade referencial,
utilizando UPSERT (ON CONFLICT (id) DO UPDATE) e ajustando sequences.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, Optional

# Garantir que o diretório raiz do projeto esteja no sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import asyncpg
from core.config import DATABASE_PATH, DATABASE_URL, normalize_database_url

TABLES_TO_MIGRATE = [
    "users",
    "accounts",
    "tasks",
    "text_rules",
    "task_messages",
    "system_logs",
    "managed_groups",
    "posts",
    "post_deliveries",
]


class RowDict(dict):
    """Subclasse de dict que permite tanto acesso por chave quanto indexação posicional row[i]."""

    def __init__(self, cols: list[str], vals: tuple | list):
        super().__init__(zip(cols, vals))
        self._seq = tuple(vals)

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._seq[key]
        return super().__getitem__(key)


def extract_sqlite_table_data(sqlite_path: str, table: str) -> tuple[list[str], list[dict]]:
    """
    Conecta ao sqlite_path com sqlite3, PRAGMA table_info para obter colunas,
    seleciona todas as linhas e retorna (column_names, rows_as_tuples_or_dicts).
    """
    if not os.path.exists(sqlite_path):
        return [], []

    conn = sqlite3.connect(sqlite_path)
    try:
        cur = conn.cursor()
        cur.execute(f"PRAGMA table_info({table})")
        pragma_info = cur.fetchall()
        if not pragma_info:
            return [], []

        cols = [col[1] for col in pragma_info]
        cur.execute(f"SELECT * FROM {table}")
        raw_rows = cur.fetchall()
        rows = [RowDict(cols, raw_row) for raw_row in raw_rows]
        return cols, rows
    finally:
        conn.close()


def build_upsert_query(table: str, columns: list[str]) -> str:
    """
    Gera query de upsert compatível com PostgreSQL:
    INSERT INTO <table> (<cols>) VALUES ($1, $2, ...)
    ON CONFLICT (id) DO UPDATE SET col1 = EXCLUDED.col1, ...
    (Se a tabela não tiver 'id', utiliza ON CONFLICT DO NOTHING).
    """
    if not columns:
        return ""

    cols_str = ", ".join(columns)
    placeholders = ", ".join(f"${i + 1}" for i in range(len(columns)))

    if "id" in columns:
        update_cols = [c for c in columns if c != "id"]
        if update_cols:
            set_clause = ", ".join(f"{col} = EXCLUDED.{col}" for col in update_cols)
            return (
                f"INSERT INTO {table} ({cols_str}) VALUES ({placeholders}) "
                f"ON CONFLICT (id) DO UPDATE SET {set_clause}"
            )
        else:
            return (
                f"INSERT INTO {table} ({cols_str}) VALUES ({placeholders}) "
                f"ON CONFLICT (id) DO NOTHING"
            )

    return (
        f"INSERT INTO {table} ({cols_str}) VALUES ({placeholders}) "
        f"ON CONFLICT DO NOTHING"
    )


async def run_migration(
    sqlite_path: Optional[str] = None,
    pg_url: Optional[str] = None,
    pg_conn: Optional[Any] = None,
) -> dict[str, int]:
    """
    Migra dados de tabelas do SQLite para PostgreSQL na ordem de dependência.
    Insere registros e ajusta as sequences no PostgreSQL.
    Retorna dicionário com contagem de registros migrados {table: count}.
    """
    resolved_sqlite = str(sqlite_path) if sqlite_path else str(DATABASE_PATH)
    resolved_pg_url = normalize_database_url(pg_url) if pg_url else DATABASE_URL

    if not os.path.exists(resolved_sqlite):
        raise FileNotFoundError(f"Arquivo SQLite não encontrado: {resolved_sqlite}")

    if pg_conn is None and not resolved_pg_url:
        raise ValueError(
            "DATABASE_URL não configurada ou fornecida. Configure DATABASE_URL no .env ou utilize --pg-url."
        )

    conn = pg_conn
    should_close_conn = False

    if conn is None:
        conn = await asyncpg.connect(resolved_pg_url)
        should_close_conn = True

    counts: dict[str, int] = {}
    try:
        for table in TABLES_TO_MIGRATE:
            cols, rows = extract_sqlite_table_data(resolved_sqlite, table)
            counts[table] = len(rows)

            if rows and cols:
                query = build_upsert_query(table, cols)
                records = [tuple(row[c] for c in cols) for row in rows]
                await conn.executemany(query, records)

                if "id" in cols:
                    seq_name = await conn.fetchval(
                        f"SELECT pg_get_serial_sequence('{table}', 'id')"
                    )
                    if seq_name:
                        await conn.execute(
                            f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), COALESCE((SELECT MAX(id) FROM {table}), 1), true)"
                        )
    finally:
        if should_close_conn and conn is not None:
            await conn.close()

    return counts


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migração automática de dados do SQLite para PostgreSQL (Telegram Cloner SaaS)"
    )
    parser.add_argument(
        "--sqlite",
        dest="sqlite_path",
        type=str,
        default=None,
        help=f"Caminho para o banco de dados SQLite de origem (padrão: {DATABASE_PATH})",
    )
    parser.add_argument(
        "--pg-url",
        dest="pg_url",
        type=str,
        default=None,
        help="URL de conexão PostgreSQL de destino (padrão: DATABASE_URL do .env)",
    )
    args = parser.parse_args()

    sqlite_source = args.sqlite_path or str(DATABASE_PATH)
    pg_target = normalize_database_url(args.pg_url or DATABASE_URL)

    print("=" * 60)
    print("       MIGRAÇÃO DE DADOS: SQLite -> PostgreSQL")
    print("=" * 60)
    print(f"Origem SQLite:      {sqlite_source}")
    print(f"Destino PostgreSQL: {pg_target or '(não configurado)'}")
    print("-" * 60)

    try:
        counts = asyncio.run(run_migration(sqlite_path=args.sqlite_path, pg_url=args.pg_url))
        print("Migração das tabelas concluída:")
        total = 0
        for table, count in counts.items():
            print(f"  ✓ {table:<16}: {count} registro(s) migrado(s)")
            total += count
        print("-" * 60)
        print(f"Migração finalizada com sucesso! Total de {total} registro(s) migrado(s).")
        print("=" * 60)
    except Exception as exc:
        print(f"\n[ERRO] Falha durante a migração: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
