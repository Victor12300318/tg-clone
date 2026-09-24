# Design: Clonagem de Álbuns (Media Groups) e Continuação Incremental de Tarefas

## 1. Visão Geral e Objetivos

Este documento especifica duas melhorias críticas no motor de clonagem do Telegram Cloner SaaS:
1. **Clonagem Fiel de Álbuns / Conjuntos de Mídia (Media Groups):** Enviar fotos e vídeos agrupados lado a lado exatamente como no canal de origem, com legenda limpa preservando nomes de catálogo e sem desmembrar em posts isolados.
2. **Continuação de Tarefas Concluídas / Interrompidas:** Permitir retomar uma tarefa já finalizada ou parada buscando somente as mensagens novas do canal de origem a partir do último ID processado, sem reiniciar do início e sem duplicar mensagens.

---

## 2. Requisitos e Regras de Negócio

### 2.1 Álbum / Media Groups
- Mensagens que compartilham o mesmo `media_group_id` no Telegram devem ser detectadas e agrupadas.
- O envio no destino deve ser feito em bloco com `client.copy_media_group` (ou `client.send_media_group` em canais com restrição de encaminhamento).
- Todos os IDs do grupo devem ser registrados no histórico de mensagens copiadas (`task_messages`) e marcados em memória (`already_copied_ids`), saltando-os no laço para não enviar mídias duplicadas.

### 2.2 Preservação de Catálogo de Modelos
- Se o texto ou legenda não possuir links (ex: apenas o nome da modelo como `"Ercilia Micarelli"`), o texto é preservado integralmente.
- Mensagens de puro texto sem links não podem ser descartadas nem ignoradas por filtros agressivos de legenda, garantindo que o índice e catálogo de modelos permaneçam organizados no canal de destino.
- Caso existam links na legenda, apenas os links/menções de spam são removidos, mantendo o nome ou texto remanescente.

### 2.3 Continuação Incremental de Tarefas
- Tarefas com status `completed`, `cancelled` ou `failed` podem ser continuadas.
- O ponto de continuação é `last_processed_id = max(task.current_message_id, max(copied_message_ids))`.
- Ao acionar "Continuar / Sincronizar", o sistema consulta a mensagem mais recente do canal de origem:
  - Se houver novas mensagens (`latest_origin_id > last_processed_id`), a tarefa é reaberta do ID `last_processed_id + 1` até `latest_origin_id`.
  - As contagens existentes (`copied_count`, `processed_messages`) são mantidas e continuam acumulando.
  - Se não houver mensagens novas, retorna aviso informando que o canal já está 100% atualizado.
- Correção do método `_progress` em `core/cloner_engine.py` para persistir `current_message_id` a cada mensagem no SQLite.

---

## 3. Arquitetura e Componentes

### 3.1 `core/cloner_engine.py`
- **Detecção de Álbuns:**
  ```python
  if message.media_group_id:
      media_group = await client.get_media_group(origin_chat, message.id)
      # Coleta todos os IDs do grupo
      group_ids = [m.id for m in media_group]
      # Transforma e envia álbum completo
      ...
  ```
- **Envio com fallback para Protegidos:**
  - Tenta `client.copy_media_group(dest_chat, origin_chat, message.id, captions=transformed_captions)`.
  - Se falhar por restrição (`_is_forwards_restricted`), faz download das mídias temporariamente e executa `client.send_media_group(dest_chat, media=...)`.
- **Regra de Texto de Catálogo:**
  - Ajuste em `apply_text_transformations` e nas verificações de texto puro para nunca descartar textos sem links.
- **Continuação de Tarefa:**
  - Novo método `sync_new_messages(owner_id: int, task_id: int) -> Dict[str, Any]` que:
    1. Localiza a tarefa e seu último ID processado.
    2. Obtém o `end_message_id` mais recente da origem via Telegram.
    3. Atualiza `start_message_id`, `end_message_id`, `current_message_id` e status para `running`.
    4. Inicia o worker em background.

### 3.2 `api/routes_tasks.py`
- Novo endpoint `POST /api/tasks/{task_id}/sync-new`:
  - Executa a continuação incremental de tarefas concluídas ou interrompidas.
  - Valida permissão do usuário autenticado (`get_current_user`).

### 3.3 Interface Web (`static/index.html` & `static/js/app.js`)
- Botão **"Continuar / Sincronizar"** nas tarefas com status `completed` (além de `cancelled` e `failed`).
- Função `syncNewMessages(taskId)` no Alpine.js que chama o endpoint e exibe feedback via toast.

---

## 4. Testes Automatizados (TDD)
- `tests/test_media_group_cloner.py`:
  - Mock de `get_media_group` e `copy_media_group`.
  - Verificação de que todos os IDs do grupo são registrados em `task_messages` e nenhum é duplicado.
  - Teste da regra de texto de catálogo (preservação de texto sem link).
- `tests/test_task_resume.py`:
  - Teste de continuação de tarefa com status `completed`.
  - Verificação de que tarefas que já estão na última mensagem não duplicam e retornam status adequado.
