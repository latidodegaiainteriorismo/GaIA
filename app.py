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
# ── HEALTH ────────────────────────────────────────────────────────────────────
@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'GaIA está viva', 'time': datetime.utcnow().isoformat()})

# ── DIAGNÓSTICO TEMPORAL (14-ago-2026) ──────────────────────────────────────
# Comprueba si Render puede resolver/llamar a la API de HuggingFace, para
# decidir el diseño de la Fase 2 de memoria (embeddings). BORRAR esta ruta
# en cuanto tengamos la respuesta — es solo para esta comprobación puntual.
@app.route('/diag/hf-test')
def diag_hf_test():
    import requests
    resultado = {}
    for url in ["https://huggingface.co", "https://api-inference.huggingface.co"]:
        try:
            r = requests.get(url, timeout=8)
            resultado[url] = f"OK — status {r.status_code}"
        except Exception as e:
            resultado[url] = f"FALLO — {type(e).__name__}: {e}"
    return jsonify(resultado)

# ── ENTRY POINT ───────────────────────────────────────────────────────────────
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    logger.info(f'🌍 GaIA arrancando en puerto {port}')
    app.run(host='0.0.0.0', port=port, debug=False)
