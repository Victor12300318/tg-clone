# Telegram Cloner SaaS

Plataforma onde operadores individuais conectam suas próprias contas Telegram para clonar, transformar e publicar conteúdo entre canais e grupos.

## Language

**Usuário**:
Pessoa que cria conta no SaaS, paga a assinatura e opera o painel. É o dono dos seus recursos (contas Telegram, tarefas, regras, posts).
_Avoid_: Cliente, tenant, account (reservado para conta Telegram)

**Conta Telegram**:
Sessão do Telegram (conta pessoal via telefone ou bot via token) conectada por um Usuário. Um Usuário pode ter várias, mas exatamente uma está ativa a cada momento — é ela que autentica as Tarefas e o publicador.
_Avoid_: Login, perfil

**Tarefa**:
Job de clonagem origem→destino (histórico ou tempo real) pertencente a um Usuário.
_Avoid_: Job, processo

**Post**:
Mensagem agendada pelo publicador para grupos gerenciados.

**Assinatura**:
Estado pago do Usuário. Um único plano flat; enquanto ativa, habilita o uso da plataforma. Não há medição de uso.
_Avoid_: Plano, licença
