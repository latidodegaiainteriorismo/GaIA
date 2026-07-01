import os
import logging
from datetime import datetime
from flask import Flask, jsonify
from flask_cors import CORS
from routes.ingest import ingest_bp
app.register_blueprint(ingest_bp)

from routes.auth          import auth_bp
from routes.conversations import conversations_bp
from routes.chat          import chat_bp

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

# ── HEALTH ────────────────────────────────────────────────────────────────────
@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'GaIA está viva', 'time': datetime.utcnow().isoformat()})

# ── ENTRY POINT ───────────────────────────────────────────────────────────────
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    logger.info(f'🌍 GaIA arrancando en puerto {port}')
    app.run(host='0.0.0.0', port=port, debug=False)
