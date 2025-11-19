import os

# Permite sobreescribir con variables de entorno en la Raspberry
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://bapldjpazhvdczjvsljd.supabase.co")
# Publishable (anon) key, NUNCA service role en el cliente
SUPABASE_KEY = os.getenv("SUPABASE_ANON_KEY", "sb_publishable_ARegOtkhHmq4TwmycVwbfA_1Awmu_tt")

LOCAL_DB = os.getenv("LOCAL_DB", "local_data.db")

# Ya no configuramos ROOM_ID/DEVICE_ID manualmente. Se descubren desde la BD.
# Puedes opcionalmente dar un nombre y ubicación para registrar o encontrar este dispositivo.
DEVICE_NAME = os.getenv("DEVICE_NAME", "")  # por defecto usaremos el hostname
DEVICE_LOCATION = os.getenv("DEVICE_LOCATION", "")

# Segundos entre sincronizaciones con la nube (optimizado para sincronización casi instantánea)
SYNC_INTERVAL = int(os.getenv("SYNC_INTERVAL", "2"))

# Backoff para reintentos del worker (en segundos)
BACKOFF_MIN = int(os.getenv("BACKOFF_MIN", "5"))
BACKOFF_MAX = int(os.getenv("BACKOFF_MAX", "300"))

# Credenciales del usuario con el que inicia sesión la Raspberry (rol authenticated)
SUPABASE_EMAIL = os.getenv("SUPABASE_EMAIL", "raspberry1@tagpass.com")
SUPABASE_PASSWORD = os.getenv("SUPABASE_PASSWORD", "123456789")

# Cada cuánto renovamos la sesión (segundos).
# Si está vacío email/password, el worker continuará como ANON sin autenticación.
AUTH_REFRESH_SECONDS = int(os.getenv("AUTH_REFRESH_SECONDS", "1800"))

# Si es true, el proceso esperará a iniciar sesión antes de continuar
AUTH_REQUIRED = os.getenv("AUTH_REQUIRED", "true").lower() in ("1", "true", "yes")

# Si Realtime no está disponible con el cliente sync, uso polling como fallback
POLL_BLOCKS_INTERVAL = int(os.getenv("POLL_BLOCKS_INTERVAL", "15"))  # segundos
