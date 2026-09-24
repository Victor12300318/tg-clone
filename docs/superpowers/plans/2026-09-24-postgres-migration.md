# Suporte Híbrido PostgreSQL + SQLite: Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) ou superpowers:executing-plans para implementar este plano tarefa por tarefa. Os passos utilizam a sintaxe de checkbox (`- [ ]`) para acompanhamento.

**Goal:** Implementar suporte transparente a PostgreSQL (via `DATABASE_URL` no EasyPanel) mantendo fallback automático para SQLite em desenvolvimento/testes, além de criar script de migração de dados de SQLite para PostgreSQL.

**Architecture:** Criação de um adaptador unificado assíncrono em `core/db_adapter.py` que abstrai as diferenças sintáticas entre `aiosqlite` e `asyncpg` (parâmetros `?` vs `$1..$n`, cursores, row mapping e `lastrowid`). Quando `DATABASE_URL` estiver preenchida, o sistema utiliza pool assíncrono do `asyncpg`; quando ausente, utiliza `aiosqlite`.

**Tech Stack:** Python 3.11+, FastAPI, asyncpg 0.29+, aiosqlite, SQLite3, PostgreSQL.

## Global Constraints

- Compatibilidade com EasyPanel: aceitar strings iniciadas tanto por `postgres://` quanto por `postgresql://`.
- Zero regressão: todos os 34 testes existentes de `pytest` devem continuar passando usando o backend SQLite sem alterações nos testes.
- Mapeamento uniforme: objetos de retorno de consulta devem permitir acesso indexado por nome de coluna (`row["email"]`).
- Suporte a transações e `commit` assíncronos em ambos os bancos.

---

### Task 1: Dependências e Configuração de Conexão

**Files:**
- Modify: `requirements.txt`
- Modify: `core/config.py`
- Modify: `.env.example`
- Test: `tests/test_database.py`

**Interfaces:**
- Produces: `DATABASE_URL: Optional[str]` em `core.config`, devidamente normalizado de `postgres://` para `postgresql://`.

- [ ] **Step 1: Escrever teste para normalização de `DATABASE_URL` em `tests/test_database.py`**

```python
def test_database_url_normalization(monkeypatch):
    import os
    from core import config
    monkeypatch.setenv("DATABASE_URL", "postgres://user:pass@host:5432/db")
    # Recarrega config ou testa função de normalização
    normalized = config.normalize_database_url("postgres://user:pass@host:5432/db")
    assert normalized == "postgresql://user:pass@host:5432/db"
```

- [ ] **Step 2: Rodar teste para verificar falha inicial**

Run: `python -m pytest tests/test_database.py -k test_database_url_normalization -v`
Expected: FAIL (AttributeError: 'normalize_database_url' não existe)

- [ ] **Step 3: Adicionar `asyncpg>=0.29.0` ao `requirements.txt` e implementar normalização em `core/config.py`**

Em `requirements.txt`:
```text
asyncpg>=0.29.0
```

Em `core/config.py`:
```python
def normalize_database_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    url = url.strip()
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://"):]
    return url

DATABASE_URL = normalize_database_url(os.getenv("DATABASE_URL", ""))
```

- [ ] **Step 4: Rodar teste para verificar sucesso**

Run: `python -m pytest tests/test_database.py -k test_database_url_normalization -v`
Expected: PASS

- [ ] **Step 5: Commit das configurações**

```bash
git add requirements.txt core/config.py .env.example tests/test_database.py
git commit -m "feat(config): adicionar asyncpg e suporte a DATABASE_URL normalizado"
```

---

### Task 2: Adaptador Unificado de Banco de Dados (`core/db_adapter.py`)

**Files:**
- Create: `core/db_adapter.py`
- Create: `tests/test_db_adapter.py`

**Interfaces:**
- Produces: `get_db_connection()`, `close_db_pool()`, `convert_query_params(query, params)`
- Consumes: `core.config.DATABASE_URL`, `core.config.DATABASE_PATH`

- [ ] **Step 1: Escrever testes unitários em `tests/test_db_adapter.py` para tradução de queries SQL**

```python
import pytest
from core.db_adapter import convert_query_for_pg, is_pg_mode

def test_convert_query_for_pg():
    query = "SELECT * FROM users WHERE email = ? AND id = ?"
    converted = convert_query_for_pg(query)
    assert converted == "SELECT * FROM users WHERE email = $1 AND id = $2"

def test_convert_query_without_placeholders():
    query = "SELECT * FROM users"
    assert convert_query_for_pg(query) == query
```

- [ ] **Step 2: Rodar teste para verificar falha**

Run: `python -m pytest tests/test_db_adapter.py -v`
Expected: FAIL (ModuleNotFoundError: No module named 'core.db_adapter')

- [ ] **Step 3: Implementar `core/db_adapter.py` com conversor e wrappers de conexão**

Implementar:
- Função `convert_query_for_pg(query: str) -> str`: substitui `?` por `$1, $2, ...` fora de strings literais.
- Classe `UnifiedCursor` e `UnifiedConnection`: fornecem `.execute()`, `.fetchone()`, `.fetchall()`, `.commit()`, `.lastrowid`.
- Suporte a pool do `asyncpg` se `DATABASE_URL` existir, ou `aiosqlite` se estiver ausente.

- [ ] **Step 4: Rodar teste para verificar sucesso**

Run: `python -m pytest tests/test_db_adapter.py -v`
Expected: PASS

- [ ] **Step 5: Commit do adaptador**

```bash
git add core/db_adapter.py tests/test_db_adapter.py
git commit -m "feat(db): implementar adaptador unificado de banco para postgres e sqlite"
```

---

### Task 3: Integração do Adaptador no `core/database.py` e `app.py`

**Files:**
- Modify: `core/database.py`
- Modify: `app.py`
- Test: `tests/` (suite completa de 34 testes existentes)

**Interfaces:**
- Consumes: `core.db_adapter.get_db_connection`, `core.db_adapter.close_db_pool`
- Produces: `init_db()`, `get_db_connection()` compatíveis com Postgres e SQLite

- [ ] **Step 1: Atualizar `init_db()` para criar tabelas com dialeto compatível (PostgreSQL e SQLite)**

- Usar `SERIAL PRIMARY KEY` no Postgres e `INTEGER PRIMARY KEY AUTOINCREMENT` no SQLite.
- Garantir que índices e foreign keys sejam idempotentes (`CREATE TABLE IF NOT EXISTS`).

- [ ] **Step 2: Fechar pool no encerramento da aplicação em `app.py`**

No bloco de shutdown do `lifespan`:
```python
await close_db_pool()
```

- [ ] **Step 3: Executar a suíte de testes inteira do projeto**

Run: `python -m pytest tests/ -q`
Expected: 35+ passed, 0 failures.

- [ ] **Step 4: Commit da integração**

```bash
git add core/database.py app.py
git commit -m "feat(database): integrar adaptador unificado em todas as operações de banco"
```

---

### Task 4: Script de Migração de Dados (`scripts/migrate_to_pg.py`)

**Files:**
- Create: `scripts/migrate_to_pg.py`
- Test: `tests/test_migration_script.py`

**Interfaces:**
- Consumes: `data/cloner.db` (SQLite local), `DATABASE_URL` (Postgres de destino).
- Produces: Execução CLI com relatório de linhas transferidas e ajuste de sequences.

- [ ] **Step 1: Escrever teste unitário para o extrator de dados da migração**

```python
import pytest
import sqlite3
import tempfile
from pathlib import Path
from scripts.migrate_to_pg import extract_sqlite_table_data

def test_extract_sqlite_data():
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
```

- [ ] **Step 2: Rodar teste para verificar falha**

Run: `python -m pytest tests/test_migration_script.py -v`
Expected: FAIL

- [ ] **Step 3: Implementar `scripts/migrate_to_pg.py`**

- Função de extração de tabelas de SQLite.
- Função de inserção em lote no PostgreSQL com `ON CONFLICT (id) DO UPDATE ...`.
- Ajuste das sequences do PostgreSQL:
  `SELECT setval(pg_get_serial_sequence('users', 'id'), COALESCE(MAX(id), 1)) FROM users;`

- [ ] **Step 4: Rodar teste e validar execução**

Run: `python -m pytest tests/test_migration_script.py -v`
Expected: PASS

- [ ] **Step 5: Commit do script de migração**

```bash
git add scripts/migrate_to_pg.py tests/test_migration_script.py
git commit -m "feat(migration): adicionar script de migracao automatica de sqlite para postgresql"
```

---

### Task 5: Docker Compose e Documentação

**Files:**
- Modify: `docker-compose.yml`
- Modify: `README.md`

- [ ] **Step 1: Atualizar `docker-compose.yml` com serviço opcional de PostgreSQL**

Adicionar serviço `postgres` com healthcheck e volume `pg-data`, configurando a env `DATABASE_URL=postgresql://postgres:postgres@postgres:5432/tgcloner`.

- [ ] **Step 2: Atualizar `README.md` com guia passo a passo do EasyPanel com PostgreSQL**

Explicar como ativar o PostgreSQL no EasyPanel em 1 clique e configurar a variável `DATABASE_URL`.

- [ ] **Step 3: Executar testes de regressão finais**

Run: `python -m pytest tests/ -q`
Expected: Todos os testes passando sem erros.

- [ ] **Step 4: Commit e envio para o GitHub**

```bash
git add docker-compose.yml README.md
git commit -m "docs: adicionar guia do postgresql no easypanel e servico no docker-compose"
git push origin main
```
