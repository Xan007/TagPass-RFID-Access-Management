from flask import Flask, request, jsonify
from db_local import (
    insert_local_event,
    is_card_blocked,
    get_counts,
)
from config import LOCAL_DB
from runtime_state import get_room_id, get_device_id, snapshot
import threading
import queue

_event_queue = queue.Queue()  # desacoplar escritura inmediata

app = Flask(__name__)

@app.route("/rfid", methods=["POST"])
def receive_rfid():
    data = request.get_json(force=True, silent=True) or {}
    card_uid = data.get("card_uid")

    if not card_uid:
        return jsonify({"error": "card_uid requerido"}), 400

    blocked = is_card_blocked(LOCAL_DB, card_uid, get_room_id() or "")
    authorized = not blocked

    # siempre registramos el intento: autorizado o denegado
    _event_queue.put((card_uid, authorized))
    return jsonify({"status": "queued", "authorized": authorized}), 202


@app.route("/status", methods=["GET"])
def status():
    counts = get_counts(LOCAL_DB)
    rs = snapshot()
    return jsonify({**rs, **counts, "queue_size": _event_queue.qsize()})


def _queue_worker():
    while True:
        item = _event_queue.get()
        try:
            card_uid, authorized = item
            insert_local_event(LOCAL_DB, card_uid, authorized)
            print(
                f"[LOCAL SERVER] Guardado evento local: {card_uid} | authorized={authorized}"
            )
            # 🔥 Despertar inmediatamente al worker para sincronizar
            try:
                from worker import realtime_event
                realtime_event.set()
            except ImportError:
                pass
        except Exception as e:
            print(f"[LOCAL SERVER] Error guardando evento {item}: {e}")
        finally:
            _event_queue.task_done()

def run_server():
    threading.Thread(target=_queue_worker, daemon=True).start()
    app.run(host="0.0.0.0", port=5000)
