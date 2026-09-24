FROM python:3.11-slim

# Variáveis de ambiente padrão
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8000 \
    RELOAD=false

# Instala dependências de sistema necessárias (ffmpeg para mídias, curl para healthcheck, gcc para compilação C se necessário)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ffmpeg \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Instalação de dependências do Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia o código da aplicação
COPY . .

# Garante que os diretórios de dados existam
RUN mkdir -p data/sessions data/tmp data/uploads

# Volume persistente para SQLite, sessões do Telegram e uploads
VOLUME ["/app/data"]

# Porta padrão exposta
EXPOSE 8000

# Healthcheck para monitorar o status do container
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8000}/ || exit 1

# Comando de inicialização
CMD ["python", "app.py"]
