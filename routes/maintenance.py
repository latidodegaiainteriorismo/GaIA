"""
routes/maintenance.py

Endpoint de mantenimiento asíncrono — "el sueño de GaIA".

Se invoca desde FUERA del ciclo de respuesta al usuario (lo pinguea
UptimeRobot cada cierto tiempo, igual que el keep-alive de /health), así que
el trabajo pesado que hace aquí NO añade latencia a las conversaciones.

Fase 1: regenera la síntesis viva de los usuarios con actividad nueva
(ver synthesis.py).
Fase 2 (futuro): este mismo endpoint generará también los embeddings
pendientes de los mensajes, para la memoria episódica semántica.

Seguridad: protegido por un token (env var MAINTENANCE_TOKEN). Se acepta
por cabecera 'X-Maintenance-Token' o por query param '?token=' — lo segundo
permite configurarlo cómodamente como una simple URL en UptimeRobot. No
expone datos: solo dispara el proceso y devuelve un resumen numérico.
"""

import os
import logging
from flask import Blueprint, request, jsonify
from synthesis import run_consolidation

logger = logging.getLogger(__name__)
maintenance_bp = Blueprint('maintenance', __name__)

_MAINTENANCE_TOKEN = os.environ.get('MAINTENANCE_TOKEN', '')

# Cuántos usuarios procesar como máximo por pasada. Con un solo usuario real
# ahora mismo basta con 3; cuando crezca, UptimeRobot pinguea cada N minutos
# y se van procesando en tandas.
_USERS_PER_RUN = 3


def _token_valido() -> bool:
    """Comprueba el token de mantenimiento (cabecera o query param)."""
    if not _MAINTENANCE_TOKEN:
        # Si no hay token configurado en el entorno, se bloquea por seguridad
        # (mejor no ejecutar que quedar abierto a cualquiera).
        logger.warning("[maintenance] MAINTENANCE_TOKEN no configurado — endpoint bloqueado")
        return False
    provided = request.headers.get('X-Maintenance-Token') or request.args.get('token', '')
    return provided == _MAINTENANCE_TOKEN


@maintenance_bp.route('/maintenance/consolidate', methods=['GET', 'POST'])
def consolidate():
    """
    Dispara una pasada de consolidación de memoria. Pensado para ser
    pingueado por UptimeRobot con la URL:
        https://<tu-backend>/maintenance/consolidate?token=XXXX
    """
    if not _token_valido():
        return jsonify({'error': 'No autorizado'}), 401

    try:
        resumen = run_consolidation(limit=_USERS_PER_RUN)
        return jsonify({'ok': True, **resumen})
    except Exception as e:
        logger.error(f"[maintenance] Error en consolidación: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500
