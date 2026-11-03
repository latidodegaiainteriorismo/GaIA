"""
Pruebas del backend de GaIA. Sin base de datos ni servicios externos: todo lo
que toca Supabase, Gemini, Groq o HuggingFace va simulado.

Cada prueba protege algo que ya se rompió una vez en producción (ver claude.md):
  - chat.py antiguo (28-ago-2026): historial ASC, sin memoria ni modo creator.
  - /ingest sin autenticación.
  - Procfile sin --timeout (CRITICAL WORKER TIMEOUT en TTS).
"""
import io
import os

import psycopg2
import psycopg2.pool
import pytest

import app as appmod
import auth
import db
import routes.audio as audio
import routes.chat as chat

H = {"Authorization": "Bearer T"}


@pytest.fixture
def client():
    return appmod.app.test_client()


@pytest.fixture
def as_creator(monkeypatch):
    """Token 'T' = usuario U1 con rol creator."""
    monkeypatch.setattr(auth, "get_user_from_token", lambda t: "U1" if t == "T" else None)
    monkeypatch.setattr(auth, "is_creator", lambda u: True)
    monkeypatch.setattr(chat, "get_user_from_token", auth.get_user_from_token)
    monkeypatch.setattr(chat, "is_creator", lambda u: True)
    monkeypatch.setattr(chat, "is_developer", lambda u: False)


# ── Seguridad ────────────────────────────────────────────────────────────────

def test_ingest_exige_login(client):
    assert client.post("/ingest", json={}).status_code == 401
    assert client.get("/ingest/models").status_code == 401


def test_chat_persistente_exige_login(client):
    assert client.post("/chat", json={"message": "x"}).status_code == 401


def test_procfile_con_timeout_e_hilos():
    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(raiz, "Procfile"), encoding="utf-8") as f:
        texto = f.read()
    assert "--timeout 120" in texto
    assert "--threads" in texto


# ── /chat ────────────────────────────────────────────────────────────────────

@pytest.fixture
def chat_env(monkeypatch, as_creator):
    """Simula BD y LLM; devuelve un dict donde se registran las llamadas."""
    rec = {"inserts": [], "fail_sources": False}
    # 30 mensajes de la BD en orden DESC (m30 el más reciente), como hace la query real
    desc = [{"role": "assistant", "content": f"m{i}"} for i in range(30, 0, -1)]

    def db_all(q, p=()):
        if "ORDER BY created_at DESC LIMIT" in q and "conversation_id" in q:
            return desc[: p[1]]
        return []

    def db_one(q, p=()):
        rec["inserts"].append(q)
        if "INSERT INTO messages" in q and "sources" in q and rec["fail_sources"]:
            return None
        if "INSERT INTO messages" in q:
            return {"id": f"id{len(rec['inserts'])}"}
        return None

    def creator_search(m, u):
        rec["creator_search"] = True
        return [{"source": "Mónica Martos.txt", "content": "c"}], ""

    def llm(history, cross, knowledge_context="", astrology_context="", extra_system_prefix=""):
        rec.update(history=history, cross=cross, knowledge=knowledge_context, prefix=extra_system_prefix)
        return "respuesta"

    monkeypatch.setattr(chat, "db_all", db_all)
    monkeypatch.setattr(chat, "db_one", db_one)
    monkeypatch.setattr(chat, "db_run", lambda q, p=(): True)
    monkeypatch.setattr(chat, "search_knowledge_creator", creator_search)
    monkeypatch.setattr(chat, "search_knowledge", lambda m: rec.__setitem__("normal_search", True) or ([], ""))
    monkeypatch.setattr(chat, "format_synthesis_context", lambda u: "SINTESIS_VIVA")
    monkeypatch.setattr(chat, "search_user_memory", lambda u, m, c=None: [1])
    monkeypatch.setattr(chat, "format_deep_memory", lambda r: "MEMORIA_PROFUNDA")
    monkeypatch.setattr(chat, "format_profile_context", lambda u: "")
    monkeypatch.setattr(chat, "needs_onboarding", lambda u: False)
    monkeypatch.setattr(chat, "detect_new_personal_data", lambda u, m: None)
    monkeypatch.setattr(chat, "extract_and_apply_save_marks", lambda u, t: t)
    monkeypatch.setattr(chat, "extract_new_chart_request", lambda m: None)
    monkeypatch.setattr(chat, "is_astrology_related", lambda m: False)
    monkeypatch.setattr(chat, "text_to_speech", lambda t, **k: None)
    monkeypatch.setattr(chat, "call_groq", llm)
    return rec


def _post_chat(client, msg="hola"):
    return client.post("/chat", headers=H,
                       json={"message": msg, "conversation_id": "C1", "voice_mode": False})


def test_chat_usa_los_mensajes_mas_recientes_en_orden(client, chat_env):
    assert _post_chat(client).status_code == 200
    h = chat_env["history"]
    assert h[0]["content"] == "m1"       # el más antiguo de los 30 cargados
    assert h[-2]["content"] == "m30"     # el más reciente
    assert h[-1]["content"] == "hola"    # el mensaje actual, el último


def test_chat_creator_usa_memoria_sintesis_y_modo_creator(client, chat_env):
    _post_chat(client)
    assert chat_env.get("creator_search") and not chat_env.get("normal_search")
    assert "MEMORIA_PROFUNDA" in chat_env["cross"]
    assert "SINTESIS_VIVA" in chat_env["prefix"]


def test_chat_devuelve_ids_de_mensaje(client, chat_env):
    d = _post_chat(client).get_json()
    assert d["text"] == "respuesta"
    assert d["user_message_id"] and d["assistant_message_id"]


def test_chat_guarda_la_respuesta_aunque_falle_la_columna_sources(client, chat_env):
    chat_env["fail_sources"] = True
    assert _post_chat(client).get_json()["assistant_message_id"]


def test_tts_y_borrar_mensajes(client, as_creator, monkeypatch):
    monkeypatch.setattr(chat, "text_to_speech_fragment", lambda t: "AUDIO")
    monkeypatch.setattr(chat, "db_run", lambda q, p=(): True)
    assert client.post("/tts", json={"text": "a"}).status_code == 401
    assert client.post("/tts", headers=H, json={"text": "a"}).get_json() == {"audio": "AUDIO"}
    assert client.delete("/messages", headers=H, json={"ids": ["a"]}).get_json() == {"deleted": True}


# ── /audio/upload ────────────────────────────────────────────────────────────

class _Transcripcion:
    text = "hola mundo"
    segments = [{"text": "hola mundo", "start": 0.0, "end": 2.0}]


@pytest.mark.parametrize("target, esperado", [("knowledge", "all"), (None, "creator")])
def test_audio_target_decide_la_visibilidad(client, as_creator, monkeypatch, target, esperado):
    capturado = []
    monkeypatch.setattr(audio, "_upload_to_storage", lambda *a, **k: True)
    monkeypatch.setattr(audio, "_transcribe", lambda b, f: _Transcripcion())
    monkeypatch.setattr(audio, "_generate_title", lambda t: "Título")
    monkeypatch.setattr(audio, "embed_text", lambda t: None)
    monkeypatch.setattr(audio, "db_one", lambda q, p=(): capturado.append(p) or {"id": "A1"})
    monkeypatch.setattr(audio, "db_run", lambda q, p=(): capturado.append(p) or True)

    data = {"audio": (io.BytesIO(b"123"), "x.webm")}
    if target:
        data["target"] = target
    d = client.post("/audio/upload", headers=H, data=data,
                    content_type="multipart/form-data").get_json()
    assert d["visibility"] == esperado
    assert capturado and all(p[-1] == esperado for p in capturado)  # audio_files y audio_chunks


# ── /health/schema ───────────────────────────────────────────────────────────

def test_health_schema_detecta_lo_que_falta(client, monkeypatch):
    todo = [{"table_name": t, "column_name": c} for t, c in appmod._SCHEMA_REQUERIDO.values()]

    def con_todo(q, p=()):
        if "information_schema" in q:
            return todo
        if "GROUP BY source" in q:
            return [{"source": "Kybalion (Fundamentos)", "n": 3}]
        # conteo por clave del catálogo: solo "Kybalion" tiene contenido
        return [{"n": 3 if "Kybalion" in p[0] else 0}]

    monkeypatch.setattr(db, "db_all", con_todo)
    d = client.get("/health/schema").get_json()
    assert d["ok"] is True
    assert d["router_catalogo"]["Kybalion"] == 3
    assert "Adrián Lozano" in d["router_sin_contenido"]
    assert "Kybalion" not in d["router_sin_contenido"]

    monkeypatch.setattr(db, "db_all", lambda q, p=(): [{"table_name": "messages", "column_name": "sources"}])
    d = client.get("/health/schema").get_json()
    assert d["ok"] is False and d["checks"]["knowledge_chunks.visibility"] is False


# ── knowledge.py: búsqueda por documento ─────────────────────────────────────

def _ilike_a_regex(patron):
    """Equivalente Python de un patrón ILIKE (% = .*, _ = un carácter), sin distinguir mayúsculas."""
    import re
    return re.compile("^" + "".join(
        ".*" if c == "%" else "." if c == "_" else re.escape(c) for c in patron) + "$", re.I | re.S)


@pytest.mark.parametrize("clave", ["Biodescodificación", "Bioneuroemoción"])
def test_el_router_encuentra_documentos_guardados_sin_tilde(clave):
    from knowledge import patron_fuente
    rx = _ilike_a_regex(patron_fuente(clave))
    # fuentes reales de producción: sin tilde en el nombre del archivo y en la carpeta
    assert rx.match("Diccionario-de-Biodescodificacion (1) (Biodescodificacion-Bioneuroemocion)")
    assert rx.match("Bioneuroemocion-Un-metodo-Enric-Corbera (Biodescodificacion-Bioneuroemocion)")
    # y siguen casando las que sí llevan tilde
    assert rx.match("Algo (Biodescodificación-Bioneuroemoción)")


def test_patron_fuente_no_casa_con_documentos_distintos():
    from knowledge import patron_fuente
    assert not _ilike_a_regex(patron_fuente("Biodescodificación")).match("Un curso de milagros")


def test_documentos_enrutados_no_repiten_chunks(monkeypatch):
    import knowledge
    mismo = {"source": "Dic (Bio)", "content": "texto", "category": "x", "rank": 1}
    monkeypatch.setattr(knowledge, "_fts_search", lambda *a, **k: [dict(mismo)])
    r = knowledge._search_routed_documents("dolor", ["Biodescodificación", "Bioneuroemoción"], ["dolor"])
    assert len(r) == 1


# ── db.py: pool de conexiones ────────────────────────────────────────────────

class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, q, p=()):
        self.conn.executed += 1
        if self.conn.fail_execute:
            raise self.conn.fail_execute
        self.conn.autocommit_visto.append(self.conn.autocommit)

    def fetchone(self):
        return {"x": 1}

    def fetchall(self):
        return [{"x": 1}, {"x": 2}]

    def close(self):
        pass


class FakeConn:
    def __init__(self, fail_execute=None, fail_commit=None):
        self.closed = 0
        self.autocommit = False
        self.executed = 0
        self.fail_execute = fail_execute
        self.fail_commit = fail_commit
        self.autocommit_visto = []

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        if self.fail_commit:
            raise self.fail_commit

    def rollback(self):
        pass

    def close(self):
        self.closed = 1


class FakePool:
    def __init__(self, *conns):
        self.pendientes = list(conns)
        self.creadas = len(conns)
        self.devueltas = []

    def getconn(self):
        if self.pendientes:
            return self.pendientes.pop(0)
        self.creadas += 1
        return FakeConn()

    def putconn(self, conn, close=False):
        self.devueltas.append((conn, close))
        if not close:
            self.pendientes.append(conn)


@pytest.fixture
def pool(monkeypatch):
    def _make(*conns):
        p = FakePool(*conns)
        monkeypatch.setattr(db, "_pool", p)
        return p
    return _make


def test_el_pool_reutiliza_la_conexion(pool):
    p = pool(FakeConn())
    for _ in range(5):
        assert db.db_one("SELECT 1") == {"x": 1}
    assert p.creadas == 1


def test_lecturas_en_autocommit_y_se_restaura(pool):
    c = FakeConn()
    pool(c)
    assert db.db_all("SELECT 1") == [{"x": 1}, {"x": 2}]
    assert c.autocommit_visto == [True] and c.autocommit is False


def test_conexion_rota_se_descarta_y_se_reintenta(pool):
    rota = FakeConn(fail_execute=psycopg2.OperationalError("server closed the connection"))
    p = pool(rota)
    assert db.db_all("SELECT 1") == [{"x": 1}, {"x": 2}]
    assert (rota, True) in p.devueltas       # la rota se descarta, no vuelve al pool
    assert p.creadas == 2                    # se abrió una nueva


def test_no_se_repite_una_escritura_si_falla_el_commit(pool):
    c = FakeConn(fail_commit=psycopg2.OperationalError("conexión perdida en el commit"))
    pool(c)
    assert db.db_run("INSERT INTO t VALUES (1)") is False
    assert c.executed == 1                   # sin reintento: podría haberse confirmado


def test_error_de_sql_devuelve_valores_vacios_y_conserva_la_conexion(pool):
    c = FakeConn(fail_execute=ValueError("columna inexistente"))
    p = pool(c)
    assert db.db_all("SELECT x") == []
    assert db.db_one("SELECT x") is None
    assert db.db_run("UPDATE x") is False
    assert all(close is False for _, close in p.devueltas)   # no es un fallo de conexión


def test_pool_agotado_usa_conexion_temporal(monkeypatch):
    class Agotado:
        def getconn(self):
            raise psycopg2.pool.PoolError("connection pool exhausted")

    temporal = FakeConn()
    monkeypatch.setattr(db, "_pool", Agotado())
    monkeypatch.setattr(db, "get_conn", lambda: temporal)
    assert db.db_one("SELECT 1") == {"x": 1}
    assert temporal.closed == 1
