import logging
from flask import Blueprint, jsonify, request, g
from auth import require_auth
from astrology import create_birth_chart_for_user, get_birth_chart_for_user, get_transits_for_user

logger        = logging.getLogger(__name__)
astrology_bp  = Blueprint('astrology', __name__)


@astrology_bp.route('/astrology/birth-chart', methods=['POST'])
@require_auth
def create_birth_chart():
    """
    Calcula y guarda la carta natal del usuario autenticado.
    Body esperado: {
        "birth_date": "1990-08-15",
        "birth_time": "16:30",       # hora LOCAL del lugar de nacimiento
        "birth_place": "Alicante, España"
    }
    """
    data        = request.get_json() or {}
    birth_date  = (data.get('birth_date') or '').strip()
    birth_time  = (data.get('birth_time') or '').strip()
    birth_place = (data.get('birth_place') or '').strip()

    if not all([birth_date, birth_time, birth_place]):
        return jsonify({'error': 'Faltan datos: birth_date, birth_time y birth_place son obligatorios'}), 400

    chart = create_birth_chart_for_user(g.user_id, birth_date, birth_time, birth_place)
    if not chart:
        return jsonify({'error': 'No se pudo calcular la carta natal. Revisa el lugar de nacimiento e inténtalo de nuevo.'}), 422

    return jsonify(chart), 201


@astrology_bp.route('/astrology/birth-chart', methods=['GET'])
@require_auth
def get_birth_chart():
    """Devuelve la carta natal guardada del usuario autenticado."""
    chart = get_birth_chart_for_user(g.user_id)
    if not chart:
        return jsonify({'error': 'Aún no tienes una carta natal guardada'}), 404
    return jsonify(chart)


@astrology_bp.route('/astrology/transits', methods=['GET'])
@require_auth
def get_transits():
    """Calcula los tránsitos actuales sobre la carta natal guardada del usuario."""
    transits = get_transits_for_user(g.user_id)
    if transits is None:
        return jsonify({'error': 'Necesitas calcular tu carta natal primero (POST /astrology/birth-chart)'}), 404
    return jsonify(transits)
