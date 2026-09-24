# Especificação Técnica: Migração Híbrida PostgreSQL + SQLite

## 1. Visão Geral e Objetivos

O objetivo deste design é permitir que a plataforma **Telegram Cloner SaaS** opere com **PostgreSQL** de alta concorrência em produção (implantado via EasyPanel através da variável `DATABASE_URL`), mantendo suporte transparente a **SQLite** como fallback para desenvolvimento local e execução rápida da suíte de testes automatizados (`pytest`).

Adicionalmente, será fornecido um script de migração (`scripts/migrate_to_pg.py`) para transferir registros existentes (usuários, sessões criptografadas do Telegram, tarefas, regras de texto, grupos gerenciados e logs) de `data/cloner.db` diretamente para a instância PostgreSQL.

---

## 2. Arquitetura do Adaptador de Banco de Dados

### 2.1 Detecção Dinâmica do Mecanismo
No módulo `core/config.py`:
- `DATABASE_URL = os.getenv("DATABASE_URL", "").strip()`
- Se `DATABASE_URL` começar com `postgres://`, é normalizado para `postgresql://` (compatibilidade padrão com drivers assíncronos modernos).
- Se `DATABASE_URL` estiver presente, o backend selecionado é **PostgreSQL** via pool `asyncpg`.
- Se `DATABASE_URL` estiver ausente ou vazio, o backend selecionado é **SQLite** via `aiosqlite` apontando para `data/cloner.db`.

### 2.2 Camada Unificada de Conexão (`DatabaseConnection`)
Para evitar reescrever ou duplicar as mais de 35 funções de negócio em `core/database.py`, criamos um wrapper unificado que expõe a mesma API assíncrona:
- `async with get_db_connection() as db:`
  - `await db.execute(query, params=None)`
  - `await cursor.fetchone()` -> Retorna dicionário ou objeto compatível com acesso por chave `row["coluna"]`.
  - `await cursor.fetchall()` -> Retorna lista de linhas indexáveis por chave.
  - `cursor.lastrowid` -> Em SQLite obtido nativamente; em PostgreSQL suportado via tradução interna ou cláusula `RETURNING id`.
  - `await db.commit()` -> Em SQLite executa commit na transação; em `asyncpg` transações usam `async with connection.transaction()`.

### 2.3 Tradução Automática de Dialeto
- **Parâmetros**: Queries com placeholders posicionais `?` são automaticamente convertidas para `$1, $2, ...` quando executadas contra PostgreSQL.
- **Tipos de Dados**:
  - `INTEGER PRIMARY KEY AUTOINCREMENT` no SQLite é mapeado para `SERIAL PRIMARY KEY` no PostgreSQL.
  - Campos booleanos: Aceitam tanto inteiros (`0/1`) quanto nativos (`True/False`) de maneira uniforme.
  - Datas e JSON: Mantêm o padrão padronizado ISO8601 string e JSON serializado em `TEXT`, garantindo zero alterações nos modelos Pydantic existentes.

---

## 3. Esquema DDL (Tabelas do Sistema)

As seguintes tabelas serão gerenciadas na inicialização (`init_db`):
1. `users` (id, email, password_hash, subscription_active, created_at)
2. `accounts` (id, owner_id, type, phone_number, api_id, api_hash, bot_token, session_string, session_name, tg_user_id, username, first_name, is_active, created_at, updated_at)
3. `tasks` (id, owner_id, name, mode, origin_chat, origin_title, dest_chat, dest_title, status, start_message_id, end_message_id, current_message_id, total_messages, processed_messages, copied_count, skipped_count, error_count, media_types_json, clean_forward, delay_seconds, skip_delay_seconds, remove_captions, remove_links, remove_mentions, header_text, footer_text, custom_replacements_json, created_at, updated_at)
4. `text_rules` (id, owner_id, pattern, replacement, is_regex, created_at, updated_at)
5. `task_messages` (id, task_id, message_id, status, error, processed_at)
6. `system_logs` (id, owner_id, task_id, level, message, created_at)
7. `managed_groups` (id, owner_id, chat_id, title, chat_type, is_admin, added_at)
8. `posts` (id, owner_id, name, text, media_type, media_path, target_group_ids_json, status, schedule_type, recurrence_rule_json, run_at, next_run_at, last_run_at, created_at, updated_at)
9. `post_deliveries` (id, post_id, chat_id, chat_title, message_id, status, error, sent_at)

---

## 4. Script de Migração de Dados (`scripts/migrate_to_pg.py`)

O script executará as etapas:
1. Conecta ao SQLite local `data/cloner.db`.
2. Conecta ao PostgreSQL através da variável `DATABASE_URL`.
3. Para cada tabela, lê todas as linhas do SQLite e executa `INSERT ... ON CONFLICT (id) DO UPDATE ...` no PostgreSQL.
4. Para cada tabela com chave primária auto-incremento, ajusta a sequence do PostgreSQL:
   `SELECT setval(pg_get_serial_sequence('tabela', 'id'), coalesce(max(id), 1)) FROM tabela;`
5. Exibe resumo com total de registros migrados por tabela.

---

## 5. Docker, EasyPanel e Variáveis de Ambiente

1. **`requirements.txt`**: Adição de `asyncpg>=0.29.0`.
2. **`Dockerfile`**: Sem alterações estruturais; `asyncpg` utiliza wheels nativos pré-compilados para Linux.
3. **`docker-compose.yml`**: Adição de um serviço `postgres` opcional para quem quiser rodar banco completo localmente com compose.
4. **`.env.example`**:
   ```env
   # PostgreSQL (Opcional - deixe vazio para usar SQLite local)
   DATABASE_URL=postgresql://postgres:senha@localhost:5432/tgclone
   ```
5. **No EasyPanel**:
   - Adicionar serviço Postgres no mesmo projeto.
   - Na aplicação do tg-clone, adicionar variável de ambiente:
     `DATABASE_URL=${POSTGRES_URL}` ou a connection string fornecida pelo EasyPanel.

---

## 6. Plano de Verificação

1. **Testes Unitários:** Executar `pytest tests/` garantindo que todos os 34 testes passem intactos no fallback SQLite.
2. **Testes do Adaptador:** Testes dedicados cobrindo conversão de sintaxe SQL (`?` para `$n`), mapeamento de linhas e inicialização DDL.
3. **Validação do Script de Migração:** Executar teste de migração simulada para verificar integridade de foreign keys e sequences.
