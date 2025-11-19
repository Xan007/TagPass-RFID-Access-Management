import threading
import time
from supabase import create_client, Client
from db_local import get_valid_unsynced_events, mark_as_synced, upsert_blocked_card, remove_blocked_card
from runtime_state import set_device, get_device_id, get_room_id
from config import (
    SUPABASE_URL,
    SUPABASE_KEY,
    LOCAL_DB,
            SYNC_INTERVAL,
    BACKOFF_MIN,
    BACKOFF_MAX,
    SUPABASE_EMAIL,
    SUPABASE_PASSWORD,
    AUTH_REFRESH_SECONDS,
    AUTH_REQUIRED,
    POLL_BLOCKS_INTERVAL,
)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# 🔥 Evento que usaremos para "reiniciar" el bucle principal
realtime_event = threading.Event()


def _auth_login_forever():
    """Mantiene una sesión iniciada con email/password si están configurados.
    Reintenta con backoff si hay errores. Refresca la sesión periódicamente.
    """
    if not SUPABASE_EMAIL or not SUPABASE_PASSWORD:
        print("[AUTH] Sin email/password. Se usará rol ANON.")
        if AUTH_REQUIRED:
            print("[AUTH] AUTH_REQUIRED=true pero faltan credenciales -> reintentando indefinidamente.")
        else:
            return

    backoff = BACKOFF_MIN
    while True:
        try:
            print("[AUTH] Iniciando sesión con email/password...")
            supabase.auth.sign_in_with_password({
                "email": SUPABASE_EMAIL,
                "password": SUPABASE_PASSWORD,
            })
            session = supabase.auth.get_session()
            user_id = getattr(getattr(session, "user", None), "id", None) if session else None
            print(f"[AUTH] Sesión activa. user_id={user_id}")
            backoff = BACKOFF_MIN

            # Renovación simple: re-login cada AUTH_REFRESH_SECONDS
            time.sleep(AUTH_REFRESH_SECONDS)
        except Exception as e:
            print(f"[AUTH] Error de autenticación: {e}. Reintentando en {backoff}s...")
            time.sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX)

def seed_blocked_from_cloud():
    """Sincroniza la lista de tarjetas bloqueadas desde Supabase al almacenamiento local."""
    print("[WORKER] Sincronizando bloqueos desde Supabase...")
    try:
        res = (
            supabase.table("access_blocks")
            .select("card_uid, room_id")
            .eq("room_id", get_room_id())
            .execute()
        )

        data = getattr(res, "data", []) or []
        blocked_cards = []
        for r in data:
            try:
                blocked_cards.append((r.get("card_uid"), r.get("room_id")))
            except Exception:
                continue

        from db_local import update_blocked_cards
        update_blocked_cards(LOCAL_DB, blocked_cards)

        print(f"[WORKER] {len(blocked_cards)} tarjetas bloqueadas sincronizadas.")
    except Exception as e:
        print(f"[WORKER] Error al sincronizar bloqueos: {e}")


def _get_valid_card_uids():
    """Obtiene la lista de card_uid válidos de Supabase (rfid_cards)."""
    try:
        res = supabase.table("rfid_cards").select("uid").execute()
        data = getattr(res, "data", []) or []
        valid_uids = {row.get("uid") for row in data if row.get("uid")}
        print(f"[WORKER] Validadas {len(valid_uids)} tarjetas en rfid_cards")
        return valid_uids
    except Exception as e:
        print(f"[WORKER] Error obteniendo tarjetas válidas: {e}")
        return set()


def sync_with_supabase():
    # Obtener tarjetas válidas de Supabase para filtrar eventos
    valid_card_uids = _get_valid_card_uids()
    
    # Obtener eventos válidos (se eliminan automáticamente los inválidos)
    events = get_valid_unsynced_events(LOCAL_DB, valid_card_uids)
    if not events:
        print("[WORKER] No hay eventos pendientes.")
        return True

    print(f"[WORKER] Subiendo {len(events)} eventos a Supabase...")

    payload = []
    ids = []
    for (event_id, card_uid, timestamp, authorized) in events:
        payload.append({
            "card_uid": card_uid,
            "raspberry_id": get_device_id(),
            "room_id": get_room_id(),
            "event_time": timestamp,
            "authorized": bool(authorized),
        })
        ids.append(event_id)

    try:
        supabase.table("access_events").insert(payload).execute()
        mark_as_synced(LOCAL_DB, ids)
        print("[WORKER] Sincronización OK.")
        return True
    except Exception as e:
        print(f"[WORKER] Error al sincronizar lote: {e}")
        return False


def _handle_permission_change(payload):
    try:
        event_type = payload.get("eventType") or payload.get("type")
        new = payload.get("new", {})
        old = payload.get("old", {})

        if event_type in ("INSERT", "UPDATE"):
            card_uid = new.get("card_uid")
            room_id = new.get("room_id") or get_room_id() or ""
            if card_uid:
                upsert_blocked_card(LOCAL_DB, card_uid, new.get("created_at"), room_id)
                print(f"[WORKER][RT] Bloqueada: {card_uid} room={room_id}")
        elif event_type == "DELETE":
            card_uid = old.get("card_uid")
            room_id = old.get("room_id") or get_room_id() or ""
            if card_uid:
                remove_blocked_card(LOCAL_DB, card_uid, room_id)
                print(f"[WORKER][RT] Desbloqueada: {card_uid} room={room_id}")

        # 🔔 Cuando llega un cambio Realtime, despertamos el bucle principal
        realtime_event.set()

    except Exception as e:
        print(f"[WORKER][RT] Error manejando cambio: {e}")


def start_realtime_listener():
    try:
        channel = supabase.channel("access_blocks_changes")
        channel.on(  # type: ignore[attr-defined]
            "postgres_changes",
            {"event": "*", "schema": "public", "table": "access_blocks", "filter": f"room_id=eq.{get_room_id()}"},
            _handle_permission_change,
        )
        channel.subscribe()  # type: ignore[attr-defined]
        print("[WORKER] Suscrito a Realtime de access_blocks")
    except Exception as e:
        msg = str(e)
        print(f"[WORKER] Error suscribiendo Realtime: {msg}")
        # Fallback: si el cliente sync no soporta Realtime, arrancamos un poller
        if "sync client" in msg.lower() or "async client" in msg.lower():
            print("[WORKER] Realtime no disponible en cliente sync: arrancando polling de access_blocks como fallback.")
            threading.Thread(target=_poll_blocked_worker, args=(POLL_BLOCKS_INTERVAL,), daemon=True).start()
        else:
            # En otros errores reintentamos el subscriber más tarde
            print("[WORKER] Error desconocido en Realtime, reintentando en background.")
            threading.Thread(target=_retry_realtime_subscribe_backoff, daemon=True).start()


def _poll_blocked_worker(interval: int):
    """Polling simple: refresca la lista de access_blocks periódicamente.
    Llama a seed_blocked_from_cloud() y despierta el bucle principal con realtime_event.
    """
    print(f"[POLL] Arrancando poller de access_blocks cada {interval}s")
    while True:
        try:
            seed_blocked_from_cloud()
            # Notificar al bucle principal para re-evaluar inmediatamente
            realtime_event.set()
        except Exception as e:
            print(f"[POLL] Error refrescando bloqueos: {e}")
        time.sleep(interval)


def _retry_realtime_subscribe_backoff():
    backoff = BACKOFF_MIN
    while True:
        try:
            print(f"[RT-RETRY] Reintentando suscribirse a Realtime en {backoff}s...")
            time.sleep(backoff)
            start_realtime_listener()
            return
        except Exception as e:
            print(f"[RT-RETRY] Error reintentando Realtime: {e}")
            time.sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX)


def _discover_or_register_device():
    """Busca el dispositivo registrado para el usuario autenticado y carga room_id.

    Comportamiento:
    - Si la sesión está autenticada, busca en `raspberry_devices` la fila con
      `registered_by = auth.uid()` y carga `device_id` y `room_id`.
    - No crea filas desde la Raspberry. Espera que un administrador cree/asigne la fila.
    - Reintenta con backoff hasta que encuentre una fila con room_id.
    """
    backoff = BACKOFF_MIN
    while True:
        try:
            session = supabase.auth.get_session()
            if not (session and getattr(session, "user", None)):
                print("[DISCOVERY] No autenticado aún; esperando sesión para poder leer la tabla.")
            else:
                user_id = session.user.id
                res = (
                    supabase.table("raspberry_devices")
                    .select("id, room_id")
                    .eq("registered_by", user_id)
                    .limit(1)
                    .execute()
                )
                data = getattr(res, "data", []) or []
                if data:
                    device_id = data[0].get("id")
                    room_id = data[0].get("room_id")
                    set_device(device_id, room_id)
                    print(f"[DISCOVERY] Encontrado dispositivo id={device_id} room_id={room_id}")
                    if room_id:
                        return
                else:
                    print(f"[DISCOVERY] No hay dispositivo registrado para user_id={user_id}. Esperando que un admin registre la fila en raspberry_devices.")

            time.sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX)
        except Exception as e:
            print(f"[DISCOVERY] Error: {e}")
            time.sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX)


def run_worker():
    # Autenticación bloqueante si AUTH_REQUIRED, de lo contrario hilo en background
    if AUTH_REQUIRED:
        print("[WORKER] AUTH_REQUIRED=true: esperando sesión antes de continuar...")
        # Intento inicial síncrono (reusa la función con un ciclo único)
        attempt = 1
        while True:
            try:
                if not SUPABASE_EMAIL or not SUPABASE_PASSWORD:
                    print("[WORKER] Credenciales faltantes. Define SUPABASE_EMAIL y SUPABASE_PASSWORD.")
                    time.sleep(BACKOFF_MIN)
                    continue
                supabase.auth.sign_in_with_password({
                    "email": SUPABASE_EMAIL,
                    "password": SUPABASE_PASSWORD,
                })
                session = supabase.auth.get_session()
                if session and session.user:
                    print(f"[WORKER] Sesión iniciada user_id={session.user.id}")
                    break
                else:
                    print("[WORKER] Sesión inválida, reintentando...")
            except Exception as e:
                print(f"[WORKER] Error login inicial ({attempt}): {e}")
            attempt += 1
            time.sleep(min(BACKOFF_MIN * attempt, BACKOFF_MAX))
        # Arranca refresco en background
        threading.Thread(target=_auth_login_forever, daemon=True).start()
    else:
        threading.Thread(target=_auth_login_forever, daemon=True).start()

    # Descubrir / registrar dispositivo antes de continuar
    threading.Thread(target=_discover_or_register_device, daemon=True).start()

    # Esperar hasta tener room_id para poder suscribir y sincronizar
    while get_room_id() is None:
        print("[WORKER] Esperando room_id asignado en raspberry_devices...")
        time.sleep(5)

    seed_blocked_from_cloud()
    threading.Thread(target=start_realtime_listener, daemon=True).start()

    backoff = BACKOFF_MIN
    while True:
        try:
            ok = sync_with_supabase()
            if ok:
                backoff = BACKOFF_MIN
            else:
                time.sleep(backoff)
                backoff = min(backoff * 2, BACKOFF_MAX)

            # 💤 Esperar el próximo ciclo o un cambio Realtime
            print(f"[WORKER] Esperando {SYNC_INTERVAL}s o evento realtime...")
            realtime_event.wait(timeout=SYNC_INTERVAL)

            # Si el evento fue activado, lo limpiamos para la siguiente espera
            if realtime_event.is_set():
                print("[WORKER] 🔄 Reiniciando bucle por cambio Realtime.")
                realtime_event.clear()

        except Exception as e:
            print(f"[WORKER] Error general: {e}")
            time.sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX)
