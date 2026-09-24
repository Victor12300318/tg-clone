# Armazenar sessões de contas pessoais Telegram

O caso de uso central do produto exige ler canais de terceiros, o que bots não conseguem (só leem chats onde são membros). Decidimos que o SaaS autentica contas pessoais via telefone e armazena as `session_string` no banco — o bem mais sensível da plataforma, criptografado em repouso com Fernet. Consequências assumidas: risco de ban da conta do Usuário é dele (comunicar no onboarding), e qualquer acesso ao banco compromete contas Telegram se a chave vazar junto.

## Considered Options

- **Só bots** — rejeitado: quebra a leitura de canais de terceiros, que é o motivo de existir do produto.
- **Conta pessoal, sessão guardada (escolhido)** — poder total, custo: custódia de credencial sensível.
