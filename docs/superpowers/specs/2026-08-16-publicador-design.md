# Spec: Módulo Publicador & Gestão de Grupos (TG Manager Pro)

**Data:** 2026-08-16  
**Status:** Aprovado para Planejamento  
**Módulo:** 1 de 3 da Suíte de Gestão de Grupos do Telegram  

---

## 1. Visão Geral e Objetivo

Transformar a aplicação em uma suíte para quem gerencia canais e grupos no Telegram. O **Módulo Publicador** resolve a dor de publicar e gerenciar conteúdo em escala através de:
- **Editor unificado (Composer):** texto formatado com suporte a Markdown/HTML e 1 mídia opcional (foto, vídeo ou documento).
- **Multi-destino:** envio em massa para múltiplos grupos e canais selecionados a partir de um registro compartilhado (`managed_groups`).
- **Agendamento flexível:** disparo imediato, agendamento único (data/hora específica) ou envio recorrente (intervalo em horas, diário ou semanal).
- **Gestão pós-publicação:** rastreamento de `message_id` por grupo para permitir **edição em massa** do texto ou **exclusão em massa** de mensagens já publicadas.
- **Observabilidade:** logs em tempo real e progresso via WebSockets, com status detalhado por grupo (`post_deliveries`).

---

## 2. Arquitetura de Alto Nível

```
┌────────────────────────────────────────────────────────┐
│ UI SPA (Tailwind + Alpine.js):                         │
│   - [users] Meus Grupos (importação e listagem)        │
│   - [megaphone] Publicador (listagem + Composer)       │
├────────────────────────────────────────────────────────┤
│ Endpoints FastAPI:                                     │
│   - /api/groups/...                                    │
│   - /api/publisher/posts...                            │
│   - /api/publisher/uploads...                          │
├────────────────────────────────────────────────────────┤
│ Motores Core (Singletons assíncronos):                 │
│   - core/publisher_engine.py (loop de varredura 20s)   │
│   - core/telegram_auth.py (sessão ativa Pyrogram)      │
│   - api/websocket.py (streaming de eventos)            │
├────────────────────────────────────────────────────────┤
│ Persistência SQLite (aiosqlite):                       │
│   - managed_groups                                     │
│   - posts                                              │
│   - post_deliveries                                    │
│ Arquivos: data/uploads/ (mídias enviadas pelo composer)│
└────────────────────────────────────────────────────────┘
```

---

## 3. Modelo de Dados (SQLite)

### 3.1 Tabela `managed_groups`
Armazena grupos e canais registrados pelo usuário para reutilização nos módulos Publicador, Moderador e Analytics.

| Coluna | Tipo | Descrição |
|---|---|---|
| `id` | INTEGER PRIMARY KEY AUTOINCREMENT | Identificador interno |
| `chat_id` | TEXT UNIQUE NOT NULL | ID Telegram (ex: `-1001234567890`) ou `@username` |
| `title` | TEXT NOT NULL | Nome legível do canal/grupo |
| `chat_type` | TEXT NOT NULL | `channel`, `supergroup`, `group` ou `chat` |
| `is_admin` | INTEGER DEFAULT 0 | `1` se a conta logada for administradora, `0` caso contrário |
| `added_at` | TEXT NOT NULL | Data ISO de adição |

### 3.2 Tabela `posts`
Armazena o conteúdo, as configurações de envio e a regra de recorrência de cada publicação.

| Coluna | Tipo | Descrição |
|---|---|---|
| `id` | INTEGER PRIMARY KEY AUTOINCREMENT | ID da postagem |
| `name` | TEXT NOT NULL | Rótulo interno para identificação (ex: "Aviso de Segunda") |
| `text` | TEXT NOT NULL | Corpo do texto (com suporte a formatação) |
| `media_path` | TEXT | Caminho local do arquivo em `data/uploads/` (se houver mídia) |
| `media_type` | TEXT | `photo`, `video`, `document` ou `null` |
| `target_group_ids_json` | TEXT NOT NULL | Array JSON de IDs de `managed_groups` destino |
| `status` | TEXT NOT NULL DEFAULT 'draft' | `draft`, `scheduled`, `publishing`, `published`, `partially_failed`, `failed`, `cancelled` |
| `schedule_type` | TEXT NOT NULL DEFAULT 'now' | `now` (imediato), `once` (único), `recurring` (recorrente) |
| `recurrence_rule_json` | TEXT | JSON com `{ "freq": "daily"|"weekly"|"interval", "interval_hours": int, "weekday": int, "time_hhmm": "09:00" }` |
| `run_at` | TEXT | Data/hora ISO do primeiro disparo agendado |
| `next_run_at` | TEXT | Data/hora ISO do próximo disparo a ser executado pelo scan loop |
| `last_run_at` | TEXT | Data/hora ISO da última execução concluída |
| `created_at` | TEXT NOT NULL | Data ISO de criação |
| `updated_at` | TEXT NOT NULL | Data ISO de atualização |

### 3.3 Tabela `post_deliveries`
Recibo de entrega de cada execução em cada grupo de destino, viabilizando relatórios e edição/exclusão em massa.

| Coluna | Tipo | Descrição |
|---|---|---|
| `id` | INTEGER PRIMARY KEY AUTOINCREMENT | Identificador da entrega |
| `post_id` | INTEGER NOT NULL | Referência ao post (FOREIGN KEY) |
| `chat_id` | TEXT NOT NULL | ID do chat onde foi enviado |
| `chat_title` | TEXT | Título do chat no momento do envio |
| `message_id` | INTEGER | ID da mensagem retornada pelo Telegram (usado para editar/apagar) |
| `status` | TEXT NOT NULL DEFAULT 'pending' | `pending`, `sent`, `failed`, `edited`, `deleted` |
| `error` | TEXT | Mensagem de erro em caso de falha |
| `sent_at` | TEXT NOT NULL | Data ISO da execução |

---

## 4. Motor de Publicação (`core/publisher_engine.py`)

### 4.1 Ciclo do Scan Loop
- Executa a cada 20 segundos em background via `asyncio.Task`.
- Consulta: `SELECT * FROM posts WHERE status = 'scheduled' AND next_run_at <= datetime('now')`.
- Para cada post localizado:
  1. Atualiza status para `publishing`.
  2. Executa a rotina `execute_post_run(post_id)`.
  3. Se `schedule_type == 'recurring'`: calcula o novo `next_run_at` usando `compute_next_run()`, grava `last_run_at = now` e retorna status para `scheduled`.
  4. Se `schedule_type != 'recurring'`: se todas as entregas tiveram sucesso, status vira `published`; se houve falha parcial, `partially_failed`; se todas falharam, `failed`.

### 4.2 Lógica de Envio por Grupo
- Itera sobre os grupos configurados em `target_group_ids_json`.
- Se tiver mídia: envia via `send_photo`, `send_video` ou `send_document` usando o arquivo em `data/uploads/` com a legenda formatada.
- Se for apenas texto: envia via `send_message`.
- Tratamento de `FloodWait`: dorme o tempo exigido e repete o envio para o mesmo grupo.
- Falha isolada em um grupo grava status `failed` em `post_deliveries` e segue imediatamente para os próximos grupos.

### 4.3 Função Pura de Recorrência (`compute_next_run`)
```python
def compute_next_run(rule: Dict[str, Any], from_dt: datetime) -> Optional[datetime]:
    """
    rule format:
      - freq = "interval": interval_hours (ex: 4 -> from_dt + timedelta(hours=4))
      - freq = "daily": time_hhmm (ex: "09:00")
      - freq = "weekly": weekday (0=segunda ... 6=domingo) + time_hhmm (ex: "14:30")
    """
```

### 4.4 Edição e Exclusão em Massa
- **Editar Texto em Massa (`bulk_edit_post`):**
  - Para cada registro em `post_deliveries` do post com `status = 'sent'` ou `'edited'`:
    - Chama `client.edit_message_text` (se texto) ou `client.edit_message_caption` (se mídia).
    - Atualiza `post_deliveries.status = 'edited'`.
- **Excluir em Massa (`bulk_delete_post`):**
  - Para cada registro em `post_deliveries` do post com `message_id` válido:
    - Chama `client.delete_messages(chat_id, message_id)`.
    - Atualiza `post_deliveries.status = 'deleted'`.

---

## 5. Endpoints REST da API

### 5.1 Grupos (`/api/groups`)
- `GET /api/groups`: Lista todos os grupos gerenciados.
- `POST /api/groups`: Adiciona um ou múltiplos grupos (permite importar em lote dos diálogos da conta).
- `DELETE /api/groups/{group_id}`: Remove o grupo do registro.

### 5.2 Publicador (`/api/publisher`)
- `GET /api/publisher/posts`: Lista todas as postagens com resumo de entregas.
- `GET /api/publisher/posts/{id}`: Detalha o post e suas entregas por grupo.
- `POST /api/publisher/posts`: Cria uma postagem (rascunho, agendada ou disparo imediato).
- `PUT /api/publisher/posts/{id}`: Atualiza o conteúdo/configuração de um post antes ou depois do envio.
- `POST /api/publisher/posts/{id}/publish-now`: Dispara o envio do post imediatamente.
- `POST /api/publisher/posts/{id}/bulk-edit`: Replica nova versão do texto nos grupos onde a mensagem já foi enviada.
- `POST /api/publisher/posts/{id}/bulk-delete`: Remove as mensagens enviadas em todos os grupos de destino.
- `DELETE /api/publisher/posts/{id}`: Remove o post do sistema (opcionalmente excluindo no Telegram).
- `POST /api/publisher/uploads`: Upload multipart de foto/vídeo/documento para `data/uploads/`.

---

## 6. Interface do Usuário (SPA)

### 6.1 Navegação Lateral
- Novo item: **Publicador** (ícone Lucide `megaphone`)
- Novo item: **Meus Grupos** (ícone Lucide `users`)

### 6.2 Aba "Meus Grupos"
- Tabela com título, tipo de chat, badge de administrador e data de cadastro.
- Modal "Importar Grupos do Telegram" com lista pesquisável dos diálogos da conta logada e seleção múltipla.

### 6.3 Aba "Publicador"
- **Visão 1: Lista de Postagens**: Cards com nome, preview do texto, badge de mídia, status em português (`Rascunho`, `Agendado`, `Publicado`, `Parcial`, `Falha`), data da próxima execução, contadores de envio (`Enviado em 8/10 grupos`) e botões de ação (Publicar Agora, Editar Conteúdo, Editar nos Grupos, Apagar nos Grupos, Excluir).
- **Visão 2: Composer (Criador de Post)**:
  - **Passo 1 (Conteúdo):** Campo de texto rico/formatado + área de drag-and-drop / upload de 1 arquivo (foto, vídeo, doc) com preview visual imediato.
  - **Passo 2 (Destinos):** Grade de seleção com checkboxes dos "Meus Grupos" cadastrados + botão "Selecionar Todos".
  - **Passo 3 (Agendamento & Revisão):** Seleção de tipo (Agora, Data/Hora Única, Recorrente com intervalo/dias/horário) + Resumo legível antes de salvar.

---

## 7. Estratégia de Testes

1. **Testes Unitários:**
   - Cálculo de próxima execução em `compute_next_run()` para intervalos, diário e semanal (incluindo viradas de mês e ano).
   - Validação de schemas Pydantic para criação e atualização de posts e regras de recorrência.
2. **Testes de Integração de API (`tests/test_publisher_api.py`):**
   - CRUD completo de `managed_groups` e importação em lote.
   - CRUD de `posts` com upload de mídia mockado.
   - Chamadas de endpoints de disparo imediato, edição em massa e exclusão em massa com banco isolado.
3. **Smoke Test E2E (`scripts/smoke_test.py`):**
   - Adição do fluxo do Publicador ao script interativo para envio real em canais de teste.

---

## 8. Fatias de Implementação

- **Fatia 1 (Base & Grupos):** Tabelas SQLite + models Pydantic + endpoints `/api/groups` + UI da aba "Meus Grupos" com importador.
- **Fatia 2 (Composer & Disparo Imediato):** Upload de mídia + endpoints `/api/publisher/posts` + UI do Composer em 3 passos + envio imediato pelo `publisher_engine`.
- **Fatia 3 (Scheduler & Recorrência):** Scan loop de 20s + lógica de `compute_next_run()` + streaming de eventos WS.
- **Fatia 4 (Gestão Pós-Publicação):** Edição em massa nos grupos + exclusão em massa nos grupos + relatório de entregas detalhado no card.
- **Fatia 5 (Testes & Polimento):** Suíte pytest completa + extensão do `smoke_test.py` + documentação.
