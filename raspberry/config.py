import os
from dotenv import load_dotenv

# Cargar variables de entorno desde .env
load_dotenv()

# Supabase Cloud Configuration
# Obtén estos valores del Supabase Dashboard
SUPABASE_URL = os.getenv("SUPABASE_URL")
if not SUPABASE_URL:
    raise ValueError("SUPABASE_URL no está configurado en .env")

# Publishable (anon) key - NUNCA uses service role key en el cliente
SUPABASE_KEY = os.getenv("SUPABASE_ANON_KEY")
if not SUPABASE_KEY:
    raise ValueError("SUPABASE_ANON_KEY no está configurado en .env")

LOCAL_DB = os.getenv("LOCAL_DB", "/var/local/tagpass.db")

# Ya no configuramos ROOM_ID/DEVICE_ID manualmente. Se descubren desde la BD.
# Puedes opcionalmente dar un nombre y ubicación para registrar o encontrar este dispositivo.
DEVICE_NAME = os.getenv("DEVICE_NAME", "")  # por defecto usaremos el hostname
DEVICE_LOCATION = os.getenv("DEVICE_LOCATION", "")

# Segundos entre sincronizaciones con la nube (optimizado para sincronización casi instantánea)
SYNC_INTERVAL = int(os.getenv("SYNC_INTERVAL", "2"))

# Backoff para reintentos del worker (en segundos)
BACKOFF_MIN = int(os.getenv("BACKOFF_MIN", "5"))
BACKOFF_MAX = int(os.getenv("BACKOFF_MAX", "300"))

# Credenciales del usuario de servicio para este dispositivo
# IMPORTANTE: Crea un usuario específico en Supabase Auth para cada Raspberry
# No uses credenciales compartidas. Este usuario debe tener permisos limitados.
SUPABASE_EMAIL = os.getenv("SUPABASE_EMAIL")
if not SUPABASE_EMAIL:
    raise ValueError("SUPABASE_EMAIL no está configurado en .env")

SUPABASE_PASSWORD = os.getenv("SUPABASE_PASSWORD")
if not SUPABASE_PASSWORD:
    raise ValueError("SUPABASE_PASSWORD no está configurado en .env")

# Cada cuánto renovamos la sesión (segundos).
# Si está vacío email/password, el worker continuará como ANON sin autenticación.
AUTH_REFRESH_SECONDS = int(os.getenv("AUTH_REFRESH_SECONDS", "1800"))

# Si es true, el proceso esperará a iniciar sesión antes de continuar
AUTH_REQUIRED = os.getenv("AUTH_REQUIRED", "true").lower() in ("1", "true", "yes")

# Si Realtime no está disponible con el cliente sync, uso polling como fallback
POLL_BLOCKS_INTERVAL = int(os.getenv("POLL_BLOCKS_INTERVAL", "15"))  # segundos
