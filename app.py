import os
import logging
from datetime import datetime
from flask import Flask, jsonify
from flask_cors import CORS
from routes.auth          import auth_bp
from routes.conversations import conversations_bp
from routes.chat          import chat_bp
from routes.ingest        import ingest_bp
from routes.astrology     import astrology_bp
from routes.dev           import dev_bp
from routes.audio         import audio_bp
from routes.learnings     import learnings_bp
# ── LOGGING ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s %(message)s'
)
logger = logging.getLogger(__name__)
# ── APP ───────────────────────────────────────────────────────────────────────
app = Flask(__name__)
CORS(app)
# ── BLUEPRINTS ────────────────────────────────────────────────────────────────
app.register_blueprint(auth_bp)
app.register_blueprint(conversations_bp)
app.register_blueprint(chat_bp)
from routes.maintenance import maintenance_bp
app.register_blueprint(maintenance_bp)
app.register_blueprint(ingest_bp)
app.register_blueprint(astrology_bp)
app.register_blueprint(dev_bp)
app.register_blueprint(audio_bp)
app.register_blueprint(learnings_bp)
# ── HEALTH ────────────────────────────────────────────────────────────────────
@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'GaIA está viva', 'time': datetime.utcnow().isoformat()})

# Columnas/tablas que el código da por existentes. Las migraciones se han
# hecho a mano en Supabase, y si falta algo db_all/db_run fallan en silencio
# (devuelven []/False) — p.ej. sin knowledge_chunks.visibility TODA búsqueda
# de conocimiento sale vacía. Solo devuelve sí/no, ningún dato.
_SCHEMA_REQUERIDO = {
    'knowledge_chunks.visibility':                         ('knowledge_chunks', 'visibility'),
    'knowledge_chunks.created_at':                         ('knowledge_chunks', 'created_at'),
    'messages.sources':                                    ('messages', 'sources'),
    'messages.embedding':                                  ('messages', 'embedding'),
    'user_synthesis.synthesis_text':                       ('user_synthesis', 'synthesis_text'),
    'basal_notes.active':                                  ('basal_notes', 'active'),
    'conversation_classification_state.last_classified_message_id':
        ('conversation_classification_state', 'last_classified_message_id'),
    'audio_files.visibility':                              ('audio_files', 'visibility'),
    'audio_chunks.visibility':                             ('audio_chunks', 'visibility'),
}

@app.route('/health/schema', methods=['GET'])
def health_schema():
    from db import db_all
    rows = db_all(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = 'public'"
    )
    existentes = {(r['table_name'], r['column_name']) for r in rows}
    checks = {nombre: par in existentes for nombre, par in _SCHEMA_REQUERIDO.items()}
    resp = {'db_reachable': bool(rows), 'ok': bool(rows) and all(checks.values()),
            'checks': checks}

    # Contenido: el router (query_router.py) elige documentos del catálogo y
    # knowledge.py los busca con `source ILIKE %clave%`. Si una clave no casa
    # con ningún chunk público, el router "elige" un documento que nunca
    # devuelve nada. Informativo: no afecta a 'ok'.
    if checks.get('knowledge_chunks.visibility'):
        from query_router import DOCUMENT_CATALOG
        fuentes = db_all(
            "SELECT source, COUNT(*) AS n FROM knowledge_chunks "
            "WHERE visibility = 'all' GROUP BY source"
        )
        catalogo = {
            clave: sum(f['n'] for f in fuentes if clave.lower() in (f['source'] or '').lower())
            for clave in DOCUMENT_CATALOG
        }
        resp['router_catalogo'] = catalogo
        resp['router_sin_contenido'] = [c for c, n in catalogo.items() if n == 0]
        resp['fuentes_publicas'] = {f['source']: f['n'] for f in fuentes}
    return jsonify(resp)
# ── ENTRY POINT ───────────────────────────────────────────────────────────────
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    logger.info(f'🌍 GaIA arrancando en puerto {port}')
    app.run(host='0.0.0.0', port=port, debug=False)
