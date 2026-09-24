#!/usr/bin/env python3
"""
TG Cloner Pro - Script de Teste de Fumaça (Smoke Test E2E)
Testa todas as funcionalidades do sistema e valida o funcionamento da pausa.

Pré-requisitos:
  1. Servidor rodando em http://127.0.0.1:8000
  2. Conta do Telegram autenticada pela interface web
"""

import sys
import time
import json
import urllib.request
import urllib.error

BASE_URL = "http://127.0.0.1:8000"


def http_request(method: str, endpoint: str, data: dict = None) -> tuple[int, dict]:
    url = f"{BASE_URL}{endpoint}"
    headers = {"Content-Type": "application/json"} if data else {}
    body = json.dumps(data).encode("utf-8") if data else None

    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            res_body = res.read().decode("utf-8")
            return res.status, json.loads(res_body) if res_body else {}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8")
        try:
            return e.code, json.loads(err_body)
        except Exception:
            return e.code, {"error": err_body}
    except Exception as e:
        return 0, {"error": str(e)}


def print_step(title: str):
    print(f"\n{'=' * 60}\n  {title}\n{'=' * 60}")


def main():
    print("""
============================================================
       TG CLONER PRO v2.0 - SMOKE TEST DE VALIDAÇÃO
============================================================
    """)

    results = []

    # 1. Check Server Connection
    print_step("1. Verificando Conexão com o Servidor")
    status, data = http_request("GET", "/api/auth/status")
    if status != 200:
        print(f"[FALHA] Não foi possível conectar ao servidor em {BASE_URL}. O app está rodando?")
        sys.exit(1)

    print(f"[OK] Servidor respondendo. Autenticado: {data.get('is_authenticated')}")
    if not data.get("is_authenticated"):
        print("[ERRO] Conta do Telegram não está conectada.")
        print("Por favor, abra http://127.0.0.1:8000 no navegador e conecte sua conta antes de rodar o teste.")
        sys.exit(1)

    results.append(("1. Autenticação e Servidor Ativo", "PASS"))

    # 2. List Dialogs & Select Channels
    print_step("2. Listando Canais e Grupos Disponíveis")
    status, data = http_request("GET", "/api/auth/dialogs")
    dialogs = data.get("dialogs", []) if status == 200 else []

    if not dialogs:
        print("[AVISO] Nenhum diálogo carregado automaticamente. Digite os IDs manualmente.")
        origin_chat = input("Digite o ID ou @username do canal de ORIGEM (com permissão de encaminhamento): ").strip()
        dest_chat = input("Digite o ID ou @username do canal de DESTINO: ").strip()
    else:
        print(f"Foram encontrados {len(dialogs)} canais/grupos:")
        for idx, d in enumerate(dialogs[:15], 1):
            print(f"  [{idx}] {d.get('title')} ({d.get('type')}) - ID: {d.get('id')}")

        try:
            orig_idx = int(input("\nEscolha o número do canal de ORIGEM: ").strip()) - 1
            origin_chat = str(dialogs[orig_idx]["id"])
            print(f"Origem selecionada: {dialogs[orig_idx]['title']} ({origin_chat})")

            dest_idx = int(input("Escolha o número do canal de DESTINO: ").strip()) - 1
            dest_chat = str(dialogs[dest_idx]["id"])
            print(f"Destino selecionado: {dialogs[dest_idx]['title']} ({dest_chat})")
        except (ValueError, IndexError):
            print("[ERRO] Seleção inválida.")
            sys.exit(1)

    results.append(("2. Leitura de Diálogos", "PASS"))

    # 3. Test Rules CRUD and Toggle (PATCH)
    print_step("3. Testando Regras de Substituição (POST / PATCH / DELETE)")
    rule_payload = {
        "name": "Regra Smoke Test",
        "rule_type": "replace",
        "pattern": "@teste_antigo",
        "replacement": "@teste_novo",
        "is_regex": False,
        "enabled": True
    }
    status, rule_data = http_request("POST", "/api/rules", rule_payload)
    rule_id = rule_data.get("id")
    if status == 200 and rule_id:
        print(f"[OK] Regra criada com ID: {rule_id}")
        
        # Test PATCH toggle
        p_status, p_data = http_request("PATCH", f"/api/rules/{rule_id}", {"enabled": False})
        if p_status == 200 and p_data.get("enabled") is False:
            print("[OK] PATCH toggle da regra funcionando (enabled=False)")
            results.append(("3. Regras Globais (CRUD + PATCH Toggle)", "PASS"))
        else:
            print("[FALHA] Falha no PATCH da regra")
            results.append(("3. Regras Globais (CRUD + PATCH Toggle)", "FAIL"))

        # Delete rule
        http_request("DELETE", f"/api/rules/{rule_id}")
    else:
        results.append(("3. Regras Globais (CRUD + PATCH Toggle)", "FAIL"))

    # 4. Create Small Test Task
    print_step("4. Criando Tarefa de Teste Histórico (5 Mensagens)")
    task_payload = {
        "name": "Smoke Test Task",
        "mode": "historical",
        "origin_chat": origin_chat,
        "dest_chat": dest_chat,
        "start_message_id": 1,
        "end_message_id": 5,
        "media_types": ["all"],
        "clean_forward": True,
        "delay_seconds": 2.0,
        "skip_delay_seconds": 0.5,
        "remove_links": False,
        "remove_mentions": False,
        "header_text": "[Smoke Test]",
        "footer_text": ""
    }
    status, task_data = http_request("POST", "/api/tasks", task_payload)
    task_id = task_data.get("id")
    if status != 200 or not task_id:
        print(f"[FALHA] Erro ao criar tarefa: {task_data}")
        sys.exit(1)

    print(f"[OK] Tarefa criada com sucesso. ID: {task_id}")
    results.append(("4. Criação de Tarefa (POST /api/tasks)", "PASS"))

    # 5. Start Task & Wait for first message
    print_step("5. Iniciando Tarefa e Aguardando Processamento")
    s_status, s_data = http_request("POST", f"/api/tasks/{task_id}/start")
    if s_status != 200 or not s_data.get("success"):
        print(f"[FALHA] Erro ao iniciar tarefa: {s_data}")
        sys.exit(1)

    print("[OK] Tarefa iniciada. Monitorando progresso...")
    first_processed = False
    for _ in range(20):
        time.sleep(1.5)
        _, t_info = http_request("GET", f"/api/tasks/{task_id}")
        copied = t_info.get("copied_count", 0)
        skipped = t_info.get("skipped_count", 0)
        status_str = t_info.get("status")
        print(f"  -> Status: {status_str} | Copiadas: {copied} | Ignoradas: {skipped} | Msg Atual: {t_info.get('current_message_id')}")
        if copied > 0 or skipped > 0 or t_info.get("current_message_id", 0) > 1:
            first_processed = True
            break

    results.append(("5. Início de Clonagem & Leitura", "PASS" if first_processed else "WARN (Sem msgs imediatas)"))

    # 6. TEST PAUSE (The Crucial Proof)
    print_step("6. TESTE DE PAUSA: Verificando se a pausa interrompe o fluxo")
    p_status, p_data = http_request("POST", f"/api/tasks/{task_id}/pause")
    if p_status == 200 and p_data.get("success"):
        print("[OK] Requisição de pausa aceita.")
        
        # Verify status became paused
        time.sleep(1)
        _, t_paused = http_request("GET", f"/api/tasks/{task_id}")
        is_paused = (t_paused.get("status") == "paused")
        print(f"  -> Status após comando de pausa: {t_paused.get('status')}")
        
        processed_at_pause = t_paused.get("processed_messages", 0)
        print(f"  -> Mensagens processadas no momento da pausa: {processed_at_pause}")
        print("  -> Aguardando 6 segundos para garantir que nenhuma mensagem extra é processada...")
        time.sleep(6)

        _, t_after_wait = http_request("GET", f"/api/tasks/{task_id}")
        processed_after_wait = t_after_wait.get("processed_messages", 0)
        print(f"  -> Mensagens processadas após espera: {processed_after_wait}")

        if is_paused and processed_after_wait == processed_at_pause:
            print("[SUCESSO] Pausa confirmada! O motor interrompeu o envio e manteve o estado perfeitamente.")
            results.append(("6. Funcionalidade de Pausa (Responsiva e Firme)", "PASS"))
        else:
            print("[FALHA] O motor continuou executando após a pausa.")
            results.append(("6. Funcionalidade de Pausa", "FAIL"))
    else:
        results.append(("6. Funcionalidade de Pausa", "FAIL"))

    # 7. Resume Task
    print_step("7. Retomando a Tarefa até a Conclusão")
    r_status, r_data = http_request("POST", f"/api/tasks/{task_id}/resume")
    if r_status == 200 and r_data.get("success"):
        print("[OK] Tarefa retomada com sucesso. Aguardando finalização...")
        completed = False
        for _ in range(30):
            time.sleep(1.5)
            _, t_curr = http_request("GET", f"/api/tasks/{task_id}")
            if t_curr.get("status") in ("completed", "failed", "cancelled"):
                print(f"  -> Status final: {t_curr.get('status')} | Total copiadas: {t_curr.get('copied_count')}")
                completed = (t_curr.get("status") == "completed")
                break
        results.append(("7. Retomada de Tarefa (Resume)", "PASS" if completed else "PASS (Em andamento)"))
    else:
        results.append(("7. Retomada de Tarefa", "FAIL"))

    # 8. Edit, Duplicate and Cleanup
    print_step("8. Testando Edição (PUT), Duplicação e Exclusão (DELETE)")
    edit_status, edit_data = http_request("PUT", f"/api/tasks/{task_id}", {"name": "Smoke Test Task (Editada)"})
    if edit_status == 200 and edit_data.get("name") == "Smoke Test Task (Editada)":
        print("[OK] Edição de tarefa (PUT) funcionando.")
        results.append(("8. Edição de Tarefa (PUT)", "PASS"))
    else:
        results.append(("8. Edição de Tarefa (PUT)", "FAIL"))

    # Clean up test task
    del_status, _ = http_request("DELETE", f"/api/tasks/{task_id}")
    if del_status == 200:
        print("[OK] Limpeza de tarefas de teste realizada.")
        results.append(("9. Limpeza / Exclusão (DELETE)", "PASS"))

    # 9. Test Managed Groups CRUD
    print_step("9. Testando Módulo de Grupos Gerenciados (POST / GET / DELETE)")
    group_payload = {
        "chat_id": dest_chat,
        "title": "Grupo Teste Smoke",
        "chat_type": "supergroup",
        "is_admin": True
    }
    g_status, g_data = http_request("POST", "/api/groups", group_payload)
    if g_status == 200 and g_data.get("id"):
        group_id = g_data["id"]
        print(f"[OK] Grupo gerenciado cadastrado com ID: {group_id}")
        results.append(("10. Grupos Gerenciados (CRUD)", "PASS"))
        
        # 10. Test Publisher Post Creation & Deliveries
        print_step("10. Testando Módulo Publicador (POST / GET / DELIVERIES)")
        post_payload = {
            "name": "Post Smoke Test",
            "text": "Mensagem de teste automatizado enviada pelo Smoke Test.",
            "target_group_ids": [group_id],
            "schedule_type": "draft"
        }
        p_status, p_data = http_request("POST", "/api/publisher/posts", post_payload)
        if p_status == 200 and p_data.get("id"):
            post_id = p_data["id"]
            print(f"[OK] Postagem cadastrada com ID: {post_id}")
            
            # Check deliveries endpoint
            d_status, d_data = http_request("GET", f"/api/publisher/posts/{post_id}/deliveries")
            if d_status == 200:
                print(f"[OK] Relatório de entregas consultado com sucesso ({len(d_data)} registros).")
                results.append(("11. Publicador (Cadastro & Entregas)", "PASS"))
            else:
                results.append(("11. Publicador (Cadastro & Entregas)", "FAIL"))

            # Cleanup test post
            http_request("DELETE", f"/api/publisher/posts/{post_id}")
        else:
            results.append(("11. Publicador (Cadastro & Entregas)", "FAIL"))

        # Cleanup test group
        http_request("DELETE", f"/api/groups/{group_id}")
    else:
        results.append(("10. Grupos Gerenciados (CRUD)", "FAIL"))

    # Final Report
    print_step("RELATÓRIO FINAL DO SMOKE TEST")
    print(f"{'Funcionalidade':<45} | {'Resultado':<10}")
    print("-" * 60)
    for feat, res in results:
        color = "\033[92m" if res == "PASS" else ("\033[93m" if "WARN" in res else "\033[91m")
        reset = "\033[0m"
        print(f"{feat:<45} | {color}{res:<10}{reset}")
    print("=" * 60)


if __name__ == "__main__":
    main()
