# Telegram Channel Cloner Pro (Web Edition)

Aplicação web moderna, assíncrona e local em Python para clonagem e sincronização em tempo real de canais e grupos do Telegram.

---

## 🌟 Funcionalidades

- **Interface SPA Moderna:** Desenvolvida com TailwindCSS, Lucide Icons, Alpine.js e princípios do *Apple Design* (translucidez com glassmorphism, fluidez em active press e tema Dark/Light).
- **Autenticação Interativa na Web:**
  - Login com conta pessoal via Telefone $\rightarrow$ Código SMS/Telegram $\rightarrow$ Senha 2FA (se ativada).
  - Suporte a autenticação de Bot com `bot_token`.
- **Modos de Clonagem:**
  - **Histórico (Batch):** Clona todo o histórico ou um intervalo específico de mensagens (ID inicial ao final) com barra de progresso em tempo real.
  - **Tempo Real (Live Sync):** Escuta e encaminha instantaneamente novas postagens publicadas no canal de origem.
- **Filtros de Mídia:** Fotos, Vídeos, Documentos (PDF, ZIP, etc.), Textos, Áudios, Mensagens de Voz, GIFs/Animações, Stickers e Enquetes (Polls).
- **Pipeline de Transformação:**
  - Reenvio Limpo (sem carimbo cinza de 'Encaminhado de...').
  - Remoção automática de links HTTP/HTTPS e links de canais `t.me/`.
  - Remoção de menções `@usuario`.
  - Substituição personalizada de palavras-chave e links de afiliados por texto ou regex.
  - Adição de Cabeçalho e Rodapé/Assinatura personalizados.
- **Controle de Ciclo de Vida:** Iniciar, Pausar, Retomar e Cancelar tarefas.
- **Tratamento Inteligente de FloodWait:** Pausa automática em limites do Telegram com contagem regressiva e retentativa segura.
- **Console de Logs ao Vivo:** Transmissão em tempo real de eventos via WebSockets com filtros por nível (Info, Success, Warning, Error).
- **Persistência SQLite Local:** Histórico de tarefas e mensagens salvas no banco `data/cloner.db` sem necessidade de banco de dados externo.

---

## 🚀 Como Executar

### 1. Instalar Dependências
```bash
pip install -r requirements.txt
```

### 2. Configurar Variáveis de Ambiente
Copie `.env.example` para `.env` e preencha:
- `SECRET_KEY` / `SESSION_SECRET` — segredos aleatórios longos (sessão do painel e criptografia das sessões Telegram em repouso)
- `TG_API_ID` / `TG_API_HASH` — credenciais do app Telegram **da plataforma** ([my.telegram.org](https://my.telegram.org))

### 3. Iniciar a Aplicação
```bash
python app.py
```

### 4. Acessar o Painel Web
Abra seu navegador em:
👉 **[http://localhost:8000](http://localhost:8000)**

Crie sua conta (email + senha) na tela inicial e conecte sua Conta Telegram pelo painel.

Documentação Swagger da API: **[http://localhost:8000/docs](http://localhost:8000/docs)**

---

## 🧪 Execução de Testes
Para rodar a suíte completa de testes automatizados:
```bash
python -m pytest tests/ -v
```
