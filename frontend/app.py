import os
from datetime import datetime
from functools import wraps
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse
from dotenv import load_dotenv
from flask import Flask, flash, redirect, render_template, request, session, url_for, jsonify
from supabase import Client, create_client

load_dotenv()

# Tablas correctas según esquema Supabase
ACCESS_EVENTS_TABLE = "access_events"
ACCESS_BLOCKS_TABLE = "access_blocks"
RFID_CARDS_TABLE = "rfid_cards"
ROOMS_TABLE = "rooms"
BUILDINGS_TABLE = "buildings"

def _create_supabase_client() -> Client:
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")

    if not url or not key:
        raise RuntimeError(
            "Missing SUPABASE_URL or SUPABASE_KEY environment variables."
        )

    return create_client(url, key)


def _login_required(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if "user" not in session:
            flash("Por favor inicia sesión para continuar.", "warning")
            return redirect(url_for("login"))
        return view_func(*args, **kwargs)

    return wrapper


def _parse_date_filter(value: str, *, end_of_day: bool = False) -> Optional[str]:
    if not value:
        return None
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
        if end_of_day:
            parsed = parsed.replace(hour=23, minute=59, second=59)
        return parsed.isoformat()
    except ValueError:
        return None


def _match_student_filter(card_data: Dict[str, Any], search_term: str) -> bool:
    """Verifica si una tarjeta RFID coincide con el filtro de búsqueda."""
    if not search_term:
        return True
    
    search_lower = search_term.lower()
    
    # Buscar en nombre, código y UID
    if card_data.get("person_name"):
        if search_lower in str(card_data["person_name"]).lower():
            return True
    if card_data.get("student_code"):
        if search_lower in str(card_data["student_code"]).lower():
            return True
    if card_data.get("uid"):
        if search_lower in str(card_data["uid"]).lower():
            return True
    
    return False


def _fetch_access_logs(client: Client, filters: Dict[str, Any]) -> Dict[str, Any]:
    """Obtiene eventos de acceso con relaciones completas."""
    try:
        # Obtener eventos
        query = (
            client.table(ACCESS_EVENTS_TABLE)
            .select("*")
        )

        # Filtros
        if filters.get("start_date"):
            query = query.gte("event_time", filters["start_date"])
        if filters.get("end_date"):
            query = query.lte("event_time", filters["end_date"])

        limit = filters.get("limit") or 50
        limit = max(1, min(limit, 500))
        
        response = query.order("event_time", desc=True).limit(limit).execute()
        
        if getattr(response, "error", None):
            raise RuntimeError(response.error)
        
        events = response.data or []
        
        # Enriquecer con datos relacionados
        enriched_events = []
        for event in events:
            enriched = dict(event)
            
            # Obtener datos de tarjeta RFID
            if event.get("card_uid"):
                try:
                    card_resp = (
                        client.table(RFID_CARDS_TABLE)
                        .select("*")
                        .eq("uid", event["card_uid"])
                        .limit(1)
                        .execute()
                    )
                    if card_resp.data and len(card_resp.data) > 0:
                        enriched["rfid_card"] = card_resp.data[0]
                except Exception as e:
                    pass
            
            # Obtener datos de sala
            if event.get("room_id"):
                try:
                    room_resp = (
                        client.table(ROOMS_TABLE)
                        .select("*")
                        .eq("id", event["room_id"])
                        .limit(1)
                        .execute()
                    )
                    if room_resp.data and len(room_resp.data) > 0:
                        room_data = room_resp.data[0]
                        enriched["room"] = room_data
                        
                        # Obtener datos de edificio
                        if room_data.get("building_id"):
                            try:
                                building_resp = (
                                    client.table(BUILDINGS_TABLE)
                                    .select("*")
                                    .eq("id", room_data["building_id"])
                                    .limit(1)
                                    .execute()
                                )
                                if building_resp.data and len(building_resp.data) > 0:
                                    enriched["building"] = building_resp.data[0]
                            except Exception as e:
                                pass
                except Exception as e:
                    pass
            
            enriched_events.append(enriched)
        
        # Aplicar filtros en memoria
        filtered_events = enriched_events
        
        if filters.get("student"):
            search_term = filters["student"].lower()
            filtered_events = [
                e for e in filtered_events
                if search_term in str(e.get("rfid_card", {}).get("person_name", "")).lower()
                or search_term in str(e.get("rfid_card", {}).get("student_code", "")).lower()
                or search_term in str(e.get("card_uid", "")).lower()
            ]
        
        if filters.get("room"):
            filtered_events = [
                e for e in filtered_events
                if e.get("room", {}).get("name", "") == filters["room"]
            ]
        
        if filters.get("building"):
            filtered_events = [
                e for e in filtered_events
                if e.get("building", {}).get("name", "") == filters["building"]
            ]
        
        return {"data": filtered_events, "error": None}
    except Exception as exc:
        return {"data": [], "error": str(exc)}


def _fetch_filter_options(client: Client) -> Dict[str, Any]:
    """Obtiene opciones para filtros: estudiantes, salones, edificios."""
    options: Dict[str, Any] = {
        "students": [],
        "students_by_name": [],
        "students_by_code": [],
        "students_by_uid": [],
        "rooms": [],
        "buildings": [],
        "rooms_by_building": {},  # Nuevo: salones agrupados por edificio
    }

    try:
        resp_students = client.table(RFID_CARDS_TABLE).select("person_name, student_code, uid").execute()
        if resp_students.data:
            names = set()
            codes = set()
            uids = set()
            all_students = set()
            for row in resp_students.data:
                # Agregar nombre si existe
                if row.get("person_name"):
                    name = str(row["person_name"]).strip()
                    names.add(name)
                    all_students.add(name)
                # Agregar código si existe
                if row.get("student_code"):
                    code = str(row["student_code"]).strip()
                    codes.add(code)
                    all_students.add(code)
                # Agregar UID si existe
                if row.get("uid"):
                    uid = str(row["uid"]).strip()
                    uids.add(uid)
                    all_students.add(uid)
            
            options["students"] = sorted(all_students, key=lambda x: x.lower())
            options["students_by_name"] = sorted(names, key=lambda x: x.lower())
            options["students_by_code"] = sorted(codes, key=lambda x: x.lower())
            options["students_by_uid"] = sorted(uids, key=lambda x: x.lower())
    except Exception:
        pass

    try:
        resp_buildings = client.table(BUILDINGS_TABLE).select("*").order("name").execute()
        if resp_buildings.data:
            options["buildings"] = [b["name"] for b in resp_buildings.data if b.get("name")]
            
            # Obtener salones por edificio
            for building in resp_buildings.data:
                building_id = building.get("id")
                building_name = building.get("name")
                try:
                    rooms_resp = (
                        client.table(ROOMS_TABLE)
                        .select("*")
                        .eq("building_id", building_id)
                        .order("name")
                        .execute()
                    )
                    if rooms_resp.data:
                        options["rooms_by_building"][building_name] = [
                            r["name"] for r in rooms_resp.data if r.get("name")
                        ]
                except Exception:
                    pass
    except Exception:
        pass

    return options


def _normalize_log_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Normaliza entrada de acceso para renderizar en templates."""
    normalized = dict(entry)
    
    rfid_card = entry.get("rfid_card", {}) or {}
    room = entry.get("room", {}) or {}
    building = entry.get("building", {}) or {}
    
    person_name = rfid_card.get("person_name") if isinstance(rfid_card, dict) else ""
    student_code = rfid_card.get("student_code") if isinstance(rfid_card, dict) else ""
    room_name = room.get("name") if isinstance(room, dict) else ""
    building_name = building.get("name") if isinstance(building, dict) else ""
    
    normalized["_student_label"] = person_name or student_code or entry.get("card_uid") or "Desconocido"
    normalized["_student_code"] = student_code or ""
    normalized["_room_label"] = room_name or "Sin dato"
    normalized["_building_label"] = building_name or "Sin dato"
    normalized["_is_authorized"] = entry.get("authorized", True)
    normalized["_timestamp"] = entry.get("event_time") or entry.get("created_at") or ""
    normalized["_room_id"] = room.get("id") if isinstance(room, dict) else entry.get("room_id")
    
    return normalized


def _block_user_card(
    client: Client,
    *,
    card_uid: str,
    room_id: str,
    reason: Optional[str] = None,
    blocked_by: Optional[str] = None,
) -> None:
    """Crea un registro de bloqueo en access_blocks."""
    payload: Dict[str, Any] = {
        "card_uid": card_uid,
        "room_id": room_id,
    }
    if reason:
        payload["reason"] = reason
    if blocked_by:
        payload["blocked_by"] = blocked_by

    response = (
        client.table(ACCESS_BLOCKS_TABLE)
        .upsert(payload)
        .execute()
    )
    if getattr(response, "error", None):
        raise RuntimeError(response.error)


def _unblock_user_card(client: Client, *, block_id: str) -> None:
    """Elimina un bloqueo."""
    response = (
        client.table(ACCESS_BLOCKS_TABLE)
        .delete()
        .eq("id", block_id)
        .execute()
    )
    if getattr(response, "error", None):
        raise RuntimeError(response.error)


def _fetch_blocked_cards(client: Client) -> List[Dict[str, Any]]:
    """Obtiene tarjetas bloqueadas con detalles."""
    try:
        blocks_resp = (
            client.table(ACCESS_BLOCKS_TABLE)
            .select("*")
            .order("created_at", desc=True)
            .limit(500)
            .execute()
        )
        
        if getattr(blocks_resp, "error", None):
            return []
        
        blocks = blocks_resp.data or []
        enriched_blocks = []
        
        for block in blocks:
            enriched = dict(block)
            
            # Obtener datos de tarjeta
            if block.get("card_uid"):
                try:
                    card_resp = (
                        client.table(RFID_CARDS_TABLE)
                        .select("*")
                        .eq("uid", block["card_uid"])
                        .single()
                        .execute()
                    )
                    if card_resp.data:
                        enriched["rfid_card"] = card_resp.data
                except Exception:
                    pass
            
            # Obtener datos de sala
            if block.get("room_id"):
                try:
                    room_resp = (
                        client.table(ROOMS_TABLE)
                        .select("*")
                        .eq("id", block["room_id"])
                        .single()
                        .execute()
                    )
                    if room_resp.data:
                        enriched["room"] = room_resp.data
                        
                        # Obtener datos de edificio
                        if room_resp.data.get("building_id"):
                            try:
                                building_resp = (
                                    client.table(BUILDINGS_TABLE)
                                    .select("*")
                                    .eq("id", room_resp.data["building_id"])
                                    .single()
                                    .execute()
                                )
                                if building_resp.data:
                                    enriched["building"] = building_resp.data
                            except Exception:
                                pass
                except Exception:
                    pass
            
            enriched_blocks.append(enriched)
        
        return enriched_blocks
    except Exception:
        return []


def _is_safe_redirect(target: str) -> bool:
    if not target:
        return False
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return (
        test_url.scheme in {"http", "https"}
        and ref_url.netloc == test_url.netloc
    )


def create_app(existing_supabase: Optional[Client] = None) -> Flask:
    app = Flask(__name__)
    app.secret_key = os.getenv("FLASK_SECRET_KEY", "change-me")
    app.supabase = existing_supabase

    @app.before_request
    def ensure_supabase_client() -> None:
        if app.supabase is None:
            app.supabase = _create_supabase_client()

    @app.route("/", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            email = request.form.get("email", "").strip()
            password = request.form.get("password", "")

            if not email or not password:
                flash("Correo y contraseña son obligatorios.", "danger")
                return render_template("login.html")

            try:
                assert app.supabase is not None
                auth_response = app.supabase.auth.sign_in_with_password(
                    {"email": email, "password": password}
                )
                user = getattr(auth_response, "user", None)
                if not user:
                    raise ValueError("No se pudo recuperar la información del usuario.")

                session["user"] = {
                    "id": user.id,
                    "email": user.email,
                }
                flash("Inicio de sesión exitoso.", "success")
                return redirect(url_for("dashboard"))
            except Exception:
                flash("Credenciales inválidas o error de autenticación.", "danger")

        if "user" in session:
            return redirect(url_for("dashboard"))

        return render_template("login.html")

    @app.route("/api/search-options/<search_type>")
    @_login_required
    def get_search_options(search_type: str):
        """Obtiene opciones de búsqueda según el tipo."""
        assert app.supabase is not None
        filter_options = _fetch_filter_options(app.supabase)
        
        options_map = {
            "name": filter_options.get("students_by_name", []),
            "code": filter_options.get("students_by_code", []),
            "uid": filter_options.get("students_by_uid", []),
        }
        
        return jsonify({"options": options_map.get(search_type, [])})

    @app.route("/dashboard")
    @_login_required
    def dashboard():
        assert app.supabase is not None
        limit_arg = request.args.get("limit", "").strip()
        try:
            parsed_limit = int(limit_arg) if limit_arg else 0
        except ValueError:
            flash("El límite debe ser un número entero.", "danger")
            parsed_limit = 0

        raw_filters = {
            "student": request.args.get("student", "").strip(),
            "room": request.args.get("room", "").strip(),
            "building": request.args.get("building", "").strip(),
            "limit": parsed_limit,
        }

        start_iso = _parse_date_filter(request.args.get("start_date", ""))
        end_iso = _parse_date_filter(
            request.args.get("end_date", ""), end_of_day=True
        )
        filters = {**raw_filters, "start_date": start_iso, "end_date": end_iso}
        logs_result = _fetch_access_logs(app.supabase, filters)
        normalized_logs = [_normalize_log_entry(row) for row in logs_result["data"]]
        filter_options = (
            _fetch_filter_options(app.supabase)
            if logs_result["error"] is None
            else {"students": [], "rooms": [], "buildings": []}
        )
        def _distinct(values):
            cleaned = []
            for item in values:
                value = "" if item is None else str(item).strip()
                if value:
                    cleaned.append(value)
            return sorted(
                dict.fromkeys(cleaned), key=lambda item: item.lower()
            )

        if not filter_options["students"]:
            filter_options["students"] = _distinct(
                [log.get("_student_label") for log in normalized_logs]
            )
        if not filter_options["rooms"]:
            filter_options["rooms"] = _distinct(
                [log.get("_room_label") for log in normalized_logs]
            )
        if not filter_options["buildings"]:
            filter_options["buildings"] = _distinct(
                [log.get("_building_label") for log in normalized_logs]
            )
        filter_defaults = {
            "student": raw_filters["student"],
            "room": raw_filters["room"],
            "building": raw_filters["building"],
            "start_date": request.args.get("start_date", ""),
            "end_date": request.args.get("end_date", ""),
            "limit": limit_arg or (raw_filters["limit"] or ""),
        }
        return render_template(
            "dashboard.html",
            user=session.get("user"),
            logs=normalized_logs,
            logs_error=logs_result["error"],
            filters=filter_defaults,
            filter_options=filter_options,
        )

    @app.route("/logout")
    def logout():
        session.pop("user", None)
        flash("Sesión cerrada correctamente.", "info")
        return redirect(url_for("login"))

    @app.route("/block-access", methods=["POST"])
    @_login_required
    def block_access():
        card_uid = request.form.get("card_uid", "").strip()
        room_id = request.form.get("room_id", "").strip()
        reason = request.form.get("reason", "").strip()
        student_name = request.form.get("student_name", "").strip()
        room_name = request.form.get("room_name", "").strip()
        next_url = request.form.get("next", "")

        if not card_uid or not room_id:
            flash(
                "Se requieren al menos el UID de tarjeta y el ID de salón para bloquear.",
                "danger",
            )
            fallback = next_url if _is_safe_redirect(next_url) else url_for("dashboard")
            return redirect(fallback)

        try:
            assert app.supabase is not None
            user_id = session.get("user", {}).get("id")
            _block_user_card(
                app.supabase,
                card_uid=card_uid,
                room_id=room_id,
                reason=reason or None,
                blocked_by=user_id,
            )
            flash(
                f"Tarjeta {card_uid} ({student_name or 'usuario'}) bloqueada "
                f"para {room_name or 'salón'} correctamente.",
                "success",
            )
        except Exception as exc:
            flash(f"No fue posible bloquear la tarjeta: {exc}", "danger")

        fallback = next_url if _is_safe_redirect(next_url) else url_for("dashboard")
        return redirect(fallback)

    @app.route("/blocked-cards")
    @_login_required
    def blocked_cards():
        """Muestra todas las tarjetas bloqueadas."""
        assert app.supabase is not None
        blocked = _fetch_blocked_cards(app.supabase)
        
        return render_template(
            "blocked_cards.html",
            user=session.get("user"),
            blocked_cards=blocked,
            total_blocked=len(blocked),
        )

    @app.route("/unblock-card/<block_id>", methods=["POST"])
    @_login_required
    def unblock_card(block_id):
        """Desbloquea una tarjeta."""
        next_url = request.form.get("next", "")
        
        try:
            assert app.supabase is not None
            _unblock_user_card(app.supabase, block_id=block_id)
            flash("Tarjeta desbloqueada correctamente.", "success")
        except Exception as exc:
            flash(f"No fue posible desbloquear la tarjeta: {exc}", "danger")
        
        fallback = next_url if _is_safe_redirect(next_url) else url_for("blocked_cards")
        return redirect(fallback)

    @app.route("/rooms")
    @_login_required
    def rooms():
        """Lista y gestiona salones."""
        assert app.supabase is not None
        
        # Obtener todos los edificios y salones
        try:
            buildings_resp = app.supabase.table(BUILDINGS_TABLE).select("*").order("name").execute()
            buildings = buildings_resp.data or [] if not getattr(buildings_resp, "error", None) else []
        except Exception:
            buildings = []
        
        # Crear mapa de building_id -> building_name para referencia rápida
        building_map = {b["id"]: b["name"] for b in buildings}
        
        rooms_by_building = {}
        try:
            rooms_resp = app.supabase.table(ROOMS_TABLE).select("*").order("name").execute()
            all_rooms = rooms_resp.data or [] if not getattr(rooms_resp, "error", None) else []
            
            for room in all_rooms:
                building_id = room.get("building_id")
                building_name = building_map.get(building_id, "Sin edificio")
                
                if building_name not in rooms_by_building:
                    rooms_by_building[building_name] = []
                rooms_by_building[building_name].append(room)
        except Exception:
            pass
        
        return render_template(
            "rooms.html",
            user=session.get("user"),
            buildings=buildings,
            rooms_by_building=rooms_by_building,
        )

    @app.route("/spaces")
    @_login_required
    def spaces():
        """Gestión unificada de edificios y salones."""
        assert app.supabase is not None
        
        # Obtener todos los edificios
        try:
            buildings_resp = app.supabase.table(BUILDINGS_TABLE).select("*").order("name").execute()
            buildings = buildings_resp.data or [] if not getattr(buildings_resp, "error", None) else []
        except Exception:
            buildings = []
        
        # Crear mapa de building_id -> building_name para referencia rápida
        building_map = {b["id"]: b["name"] for b in buildings}
        
        # Obtener todos los salones organizados por edificio
        rooms_by_building = {}
        try:
            rooms_resp = app.supabase.table(ROOMS_TABLE).select("*").order("name").execute()
            all_rooms = rooms_resp.data or [] if not getattr(rooms_resp, "error", None) else []
            
            for room in all_rooms:
                building_id = room.get("building_id")
                building_name = building_map.get(building_id, "Sin edificio")
                
                if building_name not in rooms_by_building:
                    rooms_by_building[building_name] = []
                rooms_by_building[building_name].append(room)
        except Exception:
            pass
        
        return render_template(
            "spaces.html",
            user=session.get("user"),
            buildings=buildings,
            rooms_by_building=rooms_by_building,
        )

    @app.route("/add-room", methods=["POST"])
    @_login_required
    def add_room():
        """Agrega un nuevo salón."""
        name = request.form.get("name", "").strip()
        building_id = request.form.get("building_id", "").strip()
        room_type = request.form.get("type", "AULA").strip()
        next_url = request.form.get("next", "")
        
        if not name or not building_id:
            flash("El nombre y edificio del salón son obligatorios.", "danger")
        else:
            try:
                assert app.supabase is not None
                payload = {
                    "name": name,
                    "building_id": building_id,
                    "type": room_type,
                }
                response = app.supabase.table(ROOMS_TABLE).insert(payload).execute()
                if not getattr(response, "error", None):
                    flash(f"Salón '{name}' agregado correctamente.", "success")
                else:
                    flash(f"Error al agregar salón: {response.error}", "danger")
            except Exception as exc:
                flash(f"Error al agregar salón: {exc}", "danger")
        
        fallback = next_url if _is_safe_redirect(next_url) else url_for("rooms")
        return redirect(fallback)

    @app.route("/buildings")
    @_login_required
    def buildings():
        """Lista y gestiona edificios."""
        assert app.supabase is not None
        
        try:
            buildings_resp = app.supabase.table(BUILDINGS_TABLE).select("*").order("name").execute()
            buildings_list = buildings_resp.data or [] if not getattr(buildings_resp, "error", None) else []
        except Exception:
            buildings_list = []
        
        return render_template(
            "buildings.html",
            user=session.get("user"),
            buildings=buildings_list,
        )

    @app.route("/add-building", methods=["POST"])
    @_login_required
    def add_building():
        """Agrega un nuevo edificio."""
        name = request.form.get("name", "").strip()
        next_url = request.form.get("next", "")
        
        if not name:
            flash("El nombre del edificio es obligatorio.", "danger")
        else:
            try:
                assert app.supabase is not None
                payload = {"name": name}
                response = app.supabase.table(BUILDINGS_TABLE).insert(payload).execute()
                if not getattr(response, "error", None):
                    flash(f"Edificio '{name}' agregado correctamente.", "success")
                else:
                    flash(f"Error al agregar edificio: {response.error}", "danger")
            except Exception as exc:
                flash(f"Error al agregar edificio: {exc}", "danger")
        
        fallback = next_url if _is_safe_redirect(next_url) else url_for("buildings")
        return redirect(fallback)

    @app.route("/edit-building/<building_id>", methods=["POST"])
    @_login_required
    def edit_building(building_id: str):
        """Edita un edificio existente."""
        name = request.form.get("name", "").strip()
        next_url = request.form.get("next", "")
        
        if not name:
            flash("El nombre del edificio es obligatorio.", "danger")
        else:
            try:
                assert app.supabase is not None
                payload = {"name": name}
                response = app.supabase.table(BUILDINGS_TABLE).update(payload).eq("id", building_id).execute()
                if not getattr(response, "error", None):
                    flash(f"Edificio actualizado correctamente.", "success")
                else:
                    flash(f"Error al actualizar edificio: {response.error}", "danger")
            except Exception as exc:
                flash(f"Error al actualizar edificio: {exc}", "danger")
        
        fallback = next_url if _is_safe_redirect(next_url) else url_for("buildings")
        return redirect(fallback)

    @app.route("/delete-building/<building_id>", methods=["POST"])
    @_login_required
    def delete_building(building_id: str):
        """Elimina un edificio."""
        next_url = request.form.get("next", "")
        
        # Protección contra valores inválidos (ej: 'null', '', 'undefined')
        if not building_id or str(building_id).lower() in ("null", "none", "undefined"):
            flash("ID de edificio inválido. No se puede eliminar.", "danger")
            fallback = next_url if _is_safe_redirect(next_url) else url_for("spaces")
            return redirect(fallback)

        try:
            assert app.supabase is not None
            response = app.supabase.table(BUILDINGS_TABLE).delete().eq("id", building_id).execute()
            if not getattr(response, "error", None):
                flash("Edificio eliminado correctamente.", "success")
            else:
                flash(f"Error al eliminar edificio: {response.error}", "danger")
        except Exception as exc:
            flash(f"Error al eliminar edificio: {exc}", "danger")

        fallback = next_url if _is_safe_redirect(next_url) else url_for("spaces")
        return redirect(fallback)

    @app.route("/edit-room/<room_id>", methods=["POST"])
    @_login_required
    def edit_room(room_id: str):
        """Edita un salón existente."""
        name = request.form.get("name", "").strip()
        building_id = request.form.get("building_id", "").strip()
        room_type = request.form.get("type", "AULA").strip()
        next_url = request.form.get("next", "")
        
        if not name or not building_id:
            flash("El nombre y edificio del salón son obligatorios.", "danger")
        else:
            try:
                assert app.supabase is not None
                payload = {
                    "name": name,
                    "building_id": building_id,
                    "type": room_type,
                }
                response = app.supabase.table(ROOMS_TABLE).update(payload).eq("id", room_id).execute()
                if not getattr(response, "error", None):
                    flash(f"Salón actualizado correctamente.", "success")
                else:
                    flash(f"Error al actualizar salón: {response.error}", "danger")
            except Exception as exc:
                flash(f"Error al actualizar salón: {exc}", "danger")
        
        fallback = next_url if _is_safe_redirect(next_url) else url_for("rooms")
        return redirect(fallback)

    @app.route("/delete-room/<room_id>", methods=["POST"])
    @_login_required
    def delete_room(room_id: str):
        """Elimina un salón."""
        next_url = request.form.get("next", "")
        
        try:
            assert app.supabase is not None
            response = app.supabase.table(ROOMS_TABLE).delete().eq("id", room_id).execute()
            if not getattr(response, "error", None):
                flash("Salón eliminado correctamente.", "success")
            else:
                flash(f"Error al eliminar salón: {response.error}", "danger")
        except Exception as exc:
            flash(f"Error al eliminar salón: {exc}", "danger")
        
        fallback = next_url if _is_safe_redirect(next_url) else url_for("rooms")
        return redirect(fallback)

    return app


app = create_app()


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=os.getenv("DEBUG", "false").lower() == "true")
