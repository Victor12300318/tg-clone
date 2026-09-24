# SaaS Multi-Tenant — Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Transformar o app single-user em SaaS multi-tenant onde cada Usuário (email+senha) conecta suas próprias Contas Telegram (pessoais, múltiplas com uma ativa) e opera suas Tarefas/Posts isoladamente. Sem integração de pagamento — o gate de Assinatura é um stub.

**Architecture:** Camada de Usuário (JWT-like HMAC token, scrypt) sobre o SQLite existente com `user_id` em toda tabela (ADR-0002). `TelegramAuthManager` deixa de ser singleton global e resolve o client pela conta ativa **do usuário**; sessões criptografadas em repouso com Fernet (ADR-0001). Engines (`cloner_engine`, `publisher_engine`) recebem `user_id` e o WebSocket passa a filtrar eventos por dono. Tudo continua num único processo FastAPI.

**Tech Stack:** FastAPI, Pyrogram, aiosqlite, SQLite (WAL), Alpine.js/Tailwind. **Única dependência nova:** `cryptography` (Fernet).

## Global Constraints

- **Linguagem de domínio:** Usuário, Conta Telegram, Tarefa, Post, Assinatura — conforme `CONTEXT.md`
- **Zero deps novas exceto `cryptography`** — token HMAC e hash scrypt são stdlib
- **Isolamento por filtro de dono:** toda query em `core/database.py` recebe `user_id` e filtra `WHERE user_id = ?`. Sem exceção
- **api_id/api_hash da plataforma** via `.env` (`TG_API_ID`, `TG_API_HASH`); somem dos models e da UI
- **TDD:** teste antes do código de produção para cada funcionalidade
- **Zero emojis no HTML/JS** — ícones Lucide

---

### Task 1: Usuário — schema, register/login, token

**Files:**
- Modify: `core/database.py` (tabela `users`, `create_user`, `get_user_by_email`)
- Create: `core/security.py` (hash scrypt, token HMAC assinado, `get_current_user`)
- Create: `api/routes_users.py` (`POST /api/users/register`, `POST /api/users/login`)
- Modify: `app.py` (incluir router), `core/config.py` (`SECRET_KEY` de env)
- Create: `tests/test_users.py`

**Interfaces:**
- `hash_password(pw) -> str`, `verify_password(pw, hash) -> bool` (hashlib.scrypt, salt aleatório)
- `create_token(user_id) -> str`, `verify_token(token) -> user_id` (HMAC-SHA256 sobre `user_id.exp`, stdlib `hmac`/`base64`)
- `get_current_user` — FastAPI Depends que lê `Authorization: Bearer`, valida e devolve `user_id` (401 se inválido)
- Tabela `users(id, email UNIQUE, password_hash, subscription_active INTEGER DEFAULT 1, created_at)`

- [x] Teste falhando: registrar, duplicar email (409), login com senha errada (401), login ok devolve token, `get_current_user` valida token
- [x] Implementar schema + funções + router
- [x] Aplicar `get_current_user` como dependency em TODOS os routers existentes (`routes_auth`, `routes_tasks`, `routes_rules`, `routes_groups`, `routes_publisher`) — 401 sem token

### Task 2: Tenancy no schema e nas queries

**Files:**
- Modify: `core/database.py` (todas as funções), `core/models.py`
- Create: `tests/test_tenancy.py`

**Interfaces:**
- `user_id INTEGER` adicionado a: `accounts`, `tasks`, `text_rules`, `managed_groups`, `posts`, `logs`
- Toda função pública ganha `user_id: int` como primeiro parâmetro e filtra por dono (`get_task(user_id, task_id)`, `get_all_posts(user_id)`, `delete_text_rule(user_id, rule_id)`, ...)
- `save_account(user_id, ...)`: desativa as demais contas do usuário (`UPDATE accounts SET is_active=0 WHERE user_id=?`) — **uma ativa por Usuário**
- `get_active_account(user_id)` substitui `get_active_account()`
- Regras de acesso em tabelas do outro dono retornam `None`/404, nunca dados

- [x] Teste falhando (o teste central do SaaS): Usuário A cria Tarefa, regra, grupo e post; Usuário B não vê, não edita, não apaga nada de A (todas as funções retornam vazio/None/False)
- [x] Teste: `save_account` duas vezes para o mesmo usuário → só a última fica `is_active=1`
- [x] Migrar schema (`ALTER TABLE ... ADD COLUMN user_id INTEGER`; PRAGMA user_version para versionar) e adaptar todas as funções
- [x] Todos os routers passam o `user_id` de `get_current_user` para o banco

### Task 3: Conta Telegram por Usuário + credenciais da plataforma

**Files:**
- Modify: `core/telegram_auth.py`, `core/config.py` (`TG_API_ID`, `TG_API_HASH`, `SESSION_SECRET`), `core/models.py`, `api/routes_auth.py`
- Modify: `core/security.py` (`encrypt_session`/`decrypt_session` com Fernet)
- Modify: `static/js/app.js`, `static/index.html` (remover campos api_id/api_hash do modal de login)
- Create: `tests/test_telegram_auth_user.py`

**Interfaces:**
- `TelegramAuthManager` mantém singleton, mas todo método ganha `user_id`: `send_code(user_id, phone)`, `verify_code(user_id, ...)`, `verify_password(user_id, ...)`, `bot_login(user_id, ...)`, `get_active_client(user_id)`, `get_status(user_id)`, `get_dialogs_list(user_id)`, `check_chat(user_id, ...)`, `logout(user_id)`
- Cache de clients por `(user_id)` — `Dict[int, Client]` em vez de client único
- `Client(..., api_id=settings.TG_API_ID, api_hash=settings.TG_API_HASH)` — credenciais da plataforma; `api_id`/`api_hash` removidos de `AuthSendCodeRequest`, `AuthVerifyCodeRequest`, `AuthVerifyPasswordRequest`, `AuthBotLoginRequest`, `CheckChatRequest`
- `session_string` gravada cifrada (`encrypt_session`) e lida decifrada (`decrypt_session`) — Fernet com `SESSION_SECRET` do `.env`
- `verify_code`/`verify_password`/`bot_login` salvam a conta com o `user_id` do dono

- [x] Teste falhando: `save_account` grava ciphertext (não contém o session string legível); `get_active_account` devolve legível; client de A ≠ client de B (cache por user_id)
- [x] Implementar; adicionar `cryptography` ao `requirements.txt`; criar `.env.example` com as 4 variáveis
- [x] UI: remover campos de api_id/api_hash do form e do `localStorage`

### Task 4: Engines e WebSocket escopados por usuário

**Files:**
- Modify: `core/cloner_engine.py`, `core/publisher_engine.py`, `api/websocket.py`, `core/database.py` (`add_log` com `user_id`)
- Modify: `static/js/app.js` (WS conecta com token; registra sem estar logado redireciona ao login)
- Create: `tests/test_scoped_engines.py`

**Interfaces:**
- Eventos dos engines carregam `user_id`; `WebSocketManager` guarda `user_id` por conexão e `broadcast` só envia ao dono
- `cloner_engine`: os 4 pontos de `get_active_client()` (linhas 298, 549, 781, 853) passam a `get_active_client(task.user_id)`
- `publisher_engine`: cada Post resolvido com `get_active_client(post.user_id)`
- Startup (lifespan): re-armar Tarefas live (`status='live'`) de todos os usuários a partir do banco — restart não perde sincronização em tempo real
- WS autenticado: `ws://.../ws?token=<token>` — rejeita conexão sem token válido

- [x] Teste falhando: evento com `user_id=A` não é entregue à conexão de B; add_log registra o dono
- [x] Implementar escopagem nos engines + startup re-arm
- [x] CORS: `allow_origins` de `*` para same-origin (a SPA é servida pelo próprio FastAPI)

### Task 5: Gate de Assinatura (stub) e fechamento

**Files:**
- Create: `core/subscription.py` (`require_subscription` dependency)
- Modify: `api/routes_tasks.py`, `api/routes_publisher.py` (criar Tarefa/Post exige assinatura)
- Modify: `static/js/app.js` (tela de login/registro)
- Create: `tests/test_subscription.py`

**Interfaces:**
- `require_subscription(user_id)` — Depends após `get_current_user`: 402 se `users.subscription_active = 0`. Sem provedor de pagamento; o campo é toggle manual (SQL/admin) até a integração existir
- UI: tela de login/registro (email, senha) antes de qualquer outra; guarda token no `localStorage`

- [x] Teste falhando: usuário com `subscription_active=0` recebe 402 ao criar Tarefa; leitura continua permitida
- [x] Implementar dependency + telas de login/registro
- [x] Rodar suíte completa: `python -m pytest tests/ -v`

---

## Non-goals (YAGNI explícito)

- Postgres / pool externo (ADR-0002 documenta a fuga)
- Integração de pagamento, metering, webhooks de billing
- OAuth / login social / verificação de email
- Workers/processos separados, Docker, orquestração
- Conta Telegram por Tarefa (o modelo é **uma ativa por Usuário**)
- Limites de fair-use além de um cap simples de tarefas live por usuário (constante no código, ajustável)
