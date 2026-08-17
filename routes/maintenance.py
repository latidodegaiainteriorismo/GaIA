"""
routes/maintenance.py

Endpoint de mantenimiento asíncrono — "el sueño de GaIA".

Se invoca desde FUERA del ciclo de respuesta al usuario (lo pinguea
UptimeRobot cada cierto tiempo, igual que el keep-alive de /health), así que
el trabajo pesado que hace aquí NO añade latencia a las conversaciones.

Fase 1: regenera la síntesis viva de los usuarios con actividad nueva.
Fase 2: genera embeddings pendientes de mensajes (backfill asíncrono),
        para que la búsqueda semántica de memory_search.py pueda usarlos.

Seguridad: protegido por un token (env var MAINTENANCE_TOKEN). Se acepta
por cabecera 'X-Maintenance-Token' o por query param '?token=' — lo segundo
permite configurarlo cómodamente como una simple URL en UptimeRobot.
"""

import os
import logging
from flask import Blueprint, request, jsonify
from synthesis import run_consolidation
from episodic_memory import backfill_embeddings

logger = logging.getLogger(__name__)
maintenance_bp = Blueprint('maintenance', __name__)

_MAINTENANCE_TOKEN = os.environ.get('MAINTENANCE_TOKEN', '')
_USERS_PER_RUN = 3


def _token_valido() -> bool:
    if not _MAINTENANCE_TOKEN:
        logger.warning("[maintenance] MAINTENANCE_TOKEN no configurado — endpoint bloqueado")
        return False
    provided = request.headers.get('X-Maintenance-Token') or request.args.get('token', '')
    return provided == _MAINTENANCE_TOKEN


@maintenance_bp.route('/maintenance/consolidate', methods=['GET', 'POST'])
def consolidate():
    if not _token_valido():
        return jsonify({'error': 'No autorizado'}), 401

    try:
        resumen_sintesis   = run_consolidation(limit=_USERS_PER_RUN)
        resumen_embeddings = backfill_embeddings()
        resumen = {**resumen_sintesis, **resumen_embeddings}
        return jsonify({'ok': True, **resumen})
    except Exception as e:
        logger.error(f"[maintenance] Error en consolidación: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500
