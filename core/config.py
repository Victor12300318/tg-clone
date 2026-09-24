from pathlib import Path
import os
from dotenv import load_dotenv

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

DATA_DIR = BASE_DIR / "data"
SESSIONS_DIR = DATA_DIR / "sessions"
TMP_DIR = DATA_DIR / "tmp"
UPLOADS_DIR = DATA_DIR / "uploads"
STATIC_DIR = BASE_DIR / "static"
DATABASE_PATH = DATA_DIR / "cloner.db"

# Ensure runtime directories exist
DATA_DIR.mkdir(parents=True, exist_ok=True)
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
TMP_DIR.mkdir(parents=True, exist_ok=True)
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
STATIC_DIR.mkdir(parents=True, exist_ok=True)

# Default operational settings
DEFAULT_USER_DELAY = 10.0
DEFAULT_BOT_DELAY = 1.5
DEFAULT_SKIP_DELAY = 0.5
REUPLOAD_MEMORY_LIMIT_MB = 256  # In-memory RAM limit for protected media re-upload (in MB)

# Server config
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", 8000))
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")
TG_API_ID = int(os.getenv("TG_API_ID", "0"))
TG_API_HASH = os.getenv("TG_API_HASH", "")
SESSION_SECRET = os.getenv("SESSION_SECRET", "dev-session-secret-change-me")

# Default User / Admin configuration (from .env)
DEFAULT_USER_EMAIL = os.getenv("DEFAULT_USER_EMAIL", "admin@admin.com").strip().lower()
DEFAULT_USER_PASSWORD = os.getenv("DEFAULT_USER_PASSWORD", "admin123").strip()
