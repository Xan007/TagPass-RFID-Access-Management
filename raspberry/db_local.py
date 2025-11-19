import sqlite3
from typing import Tuple
DEFAULT_ROOM_ID = ""

# Utilidad simple para obtener una conexión con WAL habilitado (mejor para escrituras concurrentes)
def _connect(db_path):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
    except Exception:
        pass
    return conn

def init_local_db(db_path):
    conn = _connect(db_path)
    c = conn.cursor()

    # Tabla de registros RFID leídos localmente
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS local_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            card_uid TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            authorized INTEGER DEFAULT 1,
            synced INTEGER DEFAULT 0
        );
        
        """
    )

    # Lista local de bloqueados
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS blocked_cards (
            card_uid TEXT NOT NULL,
            room_id TEXT NOT NULL,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(card_uid, room_id)
        );
        """
    )

    conn.commit()

    # Migraciones suaves
    _migrate_local_events_add_authorized(conn)
    _migrate_blocked_cards_add_room(conn)

    conn.close()


def _table_has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cur.fetchall())


def _migrate_local_events_add_authorized(conn: sqlite3.Connection):
    if not _table_has_column(conn, "local_events", "authorized"):
        conn.execute("ALTER TABLE local_events ADD COLUMN authorized INTEGER DEFAULT 1")
        conn.commit()


def _migrate_blocked_cards_add_room(conn: sqlite3.Connection):
    # Si la tabla antigua existía sin room_id, la migramos
    cur = conn.execute("PRAGMA table_info(blocked_cards)")
    cols = [r[1] for r in cur.fetchall()]
    if cols and ("room_id" not in cols or "card_uid" not in cols):
        # Renombrar y recrear
        conn.execute("ALTER TABLE blocked_cards RENAME TO blocked_cards_old")
        conn.execute(
            """
            CREATE TABLE blocked_cards (
                card_uid TEXT NOT NULL,
                room_id TEXT NOT NULL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(card_uid, room_id)
            )
            """
        )
        # Migrar filas antiguas con room por defecto
        try:
            for row in conn.execute("SELECT card_uid, updated_at FROM blocked_cards_old"):
                conn.execute(
                    "INSERT OR IGNORE INTO blocked_cards (card_uid, room_id, updated_at) VALUES (?, ?, ?)",
                    (row[0], DEFAULT_ROOM_ID, row[1]),
                )
        except Exception:
            pass
        conn.execute("DROP TABLE IF EXISTS blocked_cards_old")
        conn.commit()

def insert_local_event(db_path, card_uid: str, authorized: bool):
    conn = _connect(db_path)
    conn.execute(
        "INSERT INTO local_events (card_uid, authorized) VALUES (?, ?)",
        (card_uid, 1 if authorized else 0),
    )
    conn.commit()
    conn.close()

from typing import List, Any

def get_unsynced_events(db_path) -> List[Tuple[int, str, str, int]]:
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT id, card_uid, timestamp, authorized FROM local_events WHERE synced = 0"
    ).fetchall()
    conn.close()
    # rows is a list of tuples already
    return [tuple(r) for r in rows]

def mark_as_synced(db_path, ids):
    conn = _connect(db_path)
    conn.executemany("UPDATE local_events SET synced = 1 WHERE id = ?", [(i,) for i in ids])
    conn.commit()
    conn.close()


def upsert_blocked_card(db_path, card_uid, updated_at=None, room_id: str = DEFAULT_ROOM_ID):
    """Marca una tarjeta como bloqueada localmente (upsert)."""
    conn = _connect(db_path)
    if updated_at is None:
        conn.execute(
            "INSERT OR REPLACE INTO blocked_cards (card_uid, room_id, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
            (card_uid, room_id),
        )
    else:
        conn.execute(
            "INSERT OR REPLACE INTO blocked_cards (card_uid, room_id, updated_at) VALUES (?, ?, ?)",
            (card_uid, room_id, updated_at),
        )
    conn.commit()
    conn.close()


def remove_blocked_card(db_path, card_uid, room_id: str = DEFAULT_ROOM_ID):
    conn = _connect(db_path)
    conn.execute(
        "DELETE FROM blocked_cards WHERE card_uid = ? AND room_id = ?",
        (card_uid, room_id),
    )
    conn.commit()
    conn.close()


def is_card_blocked(db_path, card_uid, room_id: str = DEFAULT_ROOM_ID) -> bool:
    if room_id is None or room_id == "":
        # Sin room_id asignado aún, no bloqueamos por seguridad operativa
        return False
    conn = _connect(db_path)
    row = conn.execute(
        "SELECT 1 FROM blocked_cards WHERE card_uid = ? AND room_id = ? LIMIT 1",
        (card_uid, room_id),
    ).fetchone()
    conn.close()
    return row is not None


def get_counts(db_path):
    conn = _connect(db_path)
    cur = conn.cursor()
    unsynced = cur.execute(
        "SELECT COUNT(*) FROM local_events WHERE synced = 0"
    ).fetchone()[0]
    blocked = cur.execute("SELECT COUNT(*) FROM blocked_cards").fetchone()[0]
    total_events = cur.execute("SELECT COUNT(*) FROM local_events").fetchone()[0]
    conn.close()
    return {"unsynced": unsynced, "blocked": blocked, "total_events": total_events}

def mark_event_as_invalid(db_path, event_id: int):
    """Marca un evento como sincronizado (eliminado lógicamente) para no reintentar.
    Se usa cuando la tarjeta no existe en rfid_cards."""
    conn = _connect(db_path)
    conn.execute("UPDATE local_events SET synced = 1 WHERE id = ?", (event_id,))
    conn.commit()
    conn.close()
    print(f"[DB] Evento {event_id} marcado como sincronizado (inválido)")

def get_valid_unsynced_events(db_path, valid_card_uids: set) -> List[Tuple[int, str, str, int]]:
    """Obtiene solo eventos con card_uid válido. Automáticamente limpia inválidos."""
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT id, card_uid, timestamp, authorized FROM local_events WHERE synced = 0"
    ).fetchall()
    conn.close()
    
    valid_rows = []
    for row in rows:
        event_id, card_uid, timestamp, authorized = row
        if card_uid in valid_card_uids:
            valid_rows.append(tuple(row))
        else:
            # Limpiar evento inválido automáticamente
            mark_event_as_invalid(db_path, event_id)
            print(f"[DB] Evento {event_id} eliminado: card_uid '{card_uid}' no existe en rfid_cards")
    
    return valid_rows

def update_blocked_cards(db_path, blocked_cards):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("DELETE FROM blocked_cards")
    cur.executemany(
        "INSERT INTO blocked_cards (card_uid, room_id) VALUES (?, ?)",
        [(uid, room_id) for (uid, room_id) in blocked_cards],
    )
    conn.commit()
    conn.close()
