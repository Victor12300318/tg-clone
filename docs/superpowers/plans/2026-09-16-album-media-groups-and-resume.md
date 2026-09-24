# Clonagem de Álbuns (Media Groups) e Continuação Incremental — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Implementar suporte completo para clonagem de álbuns/conjuntos de mídias (media groups) mantendo layout e legendas/catálogo no destino, e adicionar a funcionalidade de continuar tarefas concluídas sincronizando apenas novas mensagens.

**Architecture:** 
1. No `core/cloner_engine.py`: detectar `media_group_id`, coletar todas as mídias do grupo via `client.get_media_group`, aplicar transformação preservando textos sem links e enviar em bloco com `client.copy_media_group` (com fallback de download+`send_media_group` para canais protegidos). Registrar todos os IDs no cache para não duplicar.
2. Corrigir persistência de progresso em `core/cloner_engine.py` e criar método `sync_new_messages` para consultar o canal de origem e continuar a clonagem a partir do último ID copiado até o ID mais recente.
3. Expor `POST /api/tasks/{task_id}/sync-new` em `api/routes_tasks.py`.
4. Atualizar `static/index.html` e `static/js/app.js` para exibir o botão "Continuar / Sincronizar" nas tarefas concluídas.

**Tech Stack:** Python 3.10+, FastAPI, Pyrogram, aiosqlite, Alpine.js, TailwindCSS, pytest.

## Global Constraints
- Zero novas dependências
- Preservação estrita de textos sem links (catálogo de modelos)
- Tratamento de `FloodWait` e restrição de encaminhamento em álbuns
- TDD com suíte de testes passando 100%

---

### Task 1: Correção de Progresso e Preservação de Texto de Catálogo
**Files:**
- Modify: `core/cloner_engine.py`
- Test: `tests/test_text_catalog.py`

- [x] Step 1: Escrever teste unitário para preservação de textos/legendas sem links (catálogo)
- [x] Step 2: Corrigir `self._progress` em `core/cloner_engine.py` para chamar `update_task_progress` no banco
- [x] Step 3: Ajustar `apply_text_transformations` e laço de texto puro para nunca descartar textos sem links
- [x] Step 4: Rodar testes e garantir aprovação

### Task 2: Clonagem Fiel de Álbuns (Media Groups)
**Files:**
- Modify: `core/cloner_engine.py`
- Test: `tests/test_media_group_cloner.py`

- [x] Step 1: Escrever teste unitário mockando Pyrogram `get_media_group` e `copy_media_group`
- [x] Step 2: Implementar detecção de `message.media_group_id` no laço do cloner
- [x] Step 3: Implementar envio do álbum agrupado com tratamento de legenda e fallback para canais protegidos
- [x] Step 4: Registrar todos os IDs do álbum em `task_messages` e avançar o ponteiro de leitura
- [x] Step 5: Rodar testes e verificar aprovação

### Task 3: Continuação Incremental de Tarefas
**Files:**
- Modify: `core/cloner_engine.py`
- Modify: `api/routes_tasks.py`
- Test: `tests/test_task_sync_new.py`

- [x] Step 1: Escrever teste para o endpoint `/api/tasks/{id}/sync-new`
- [x] Step 2: Implementar `sync_new_messages(owner_id, task_id)` no `cloner_engine`
- [x] Step 3: Adicionar endpoint `POST /api/tasks/{task_id}/sync-new` no `routes_tasks.py`
- [x] Step 4: Rodar testes e verificar aprovação

### Task 4: Interface Web (Botão Continuar / Sincronizar)
**Files:**
- Modify: `static/index.html`
- Modify: `static/js/app.js`

- [x] Step 1: Adicionar botão "Continuar" para tarefas com status `completed` em `static/index.html`
- [x] Step 2: Adicionar método `syncNewMessages(id)` em `static/js/app.js`
- [x] Step 3: Verificar testes e sintaxe de todo o projeto
