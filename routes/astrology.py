import logging
from flask import Blueprint, jsonify, request, g, Response
from auth import require_auth
from astrology import (
    create_birth_chart_for_user, get_birth_chart_for_user, get_transits_for_user,
    get_transits_for_user_on_date, get_birth_chart_svg_for_user, get_transit_chart_svg_for_user,
    format_astrology_context, extract_date_from_message, list_charts_for_user,
    delete_birth_chart_for_user, DEFAULT_PERSON_LABEL,
)
from llm import call_groq_astrology, GroqRateLimitError

logger        = logging.getLogger(__name__)
astrology_bp  = Blueprint('astrology', __name__)


@astrology_bp.route('/astrology/birth-chart', methods=['POST'])
@require_auth
def create_birth_chart():
    """
    Calcula y guarda una carta natal para el usuario autenticado — la suya
    propia por defecto, o la de otra persona si se especifica person_label.

    Body esperado: {
        "birth_date": "1990-08-15",
        "birth_time": "16:30",           # hora LOCAL del lugar de nacimiento
        "birth_place": "Alicante, España",
        "person_label": "Marco (hijo)",  // opcional, por defecto "yo"
        "relationship": "hijo"            // opcional, libre
    }
    """
    data          = request.get_json() or {}
    birth_date    = (data.get('birth_date') or '').strip()
    birth_time    = (data.get('birth_time') or '').strip()
    birth_place   = (data.get('birth_place') or '').strip()
    person_label  = (data.get('person_label') or DEFAULT_PERSON_LABEL).strip()
    relationship  = (data.get('relationship') or '').strip() or None

    if not all([birth_date, birth_time, birth_place]):
        return jsonify({'error': 'Faltan datos: birth_date, birth_time y birth_place son obligatorios'}), 400

    chart = create_birth_chart_for_user(g.user_id, birth_date, birth_time, birth_place,
                                        person_label, relationship)
    if not chart:
        return jsonify({'error': 'No se pudo calcular la carta natal. Revisa el lugar de nacimiento e inténtalo de nuevo.'}), 422

    return jsonify(chart), 201


@astrology_bp.route('/astrology/birth-chart', methods=['GET'])
@require_auth
def get_birth_chart():
    """
    Devuelve una carta natal guardada del usuario autenticado.
    Query param opcional: ?person=Marco (hijo) — por defecto la propia ("yo").
    """
    person_label = request.args.get('person', DEFAULT_PERSON_LABEL).strip() or DEFAULT_PERSON_LABEL
    chart = get_birth_chart_for_user(g.user_id, person_label)
    if not chart:
        return jsonify({'error': 'No hay carta natal guardada para esa persona'}), 404
    return jsonify(chart)


@astrology_bp.route('/astrology/charts', methods=['GET'])
@require_auth
def list_charts():
    """Lista todas las personas con carta natal guardada bajo este usuario."""
    charts = list_charts_for_user(g.user_id)
    return jsonify(charts)


@astrology_bp.route('/astrology/birth-chart', methods=['DELETE'])
@require_auth
def delete_birth_chart():
    """
    Elimina la carta natal de una persona concreta.
    Query param requerido: ?person=Marco (hijo)
    """
    person_label = request.args.get('person', '').strip()
    if not person_label:
        return jsonify({'error': 'Falta el parámetro person'}), 400
    if person_label == DEFAULT_PERSON_LABEL:
        return jsonify({'error': 'No puedes eliminar tu propia carta desde aquí'}), 400
    delete_birth_chart_for_user(g.user_id, person_label)
    return jsonify({'status': 'ok'})


@astrology_bp.route('/astrology/transits', methods=['GET'])
@require_auth
def get_transits():
    """
    Calcula los tránsitos sobre una carta natal guardada.
    Query params opcionales: ?date=YYYY-MM-DD, ?person=Marco (hijo)
    """
    target_date  = request.args.get('date', '').strip() or None
    person_label = request.args.get('person', DEFAULT_PERSON_LABEL).strip() or DEFAULT_PERSON_LABEL

    if target_date:
        transits = get_transits_for_user_on_date(g.user_id, target_date, person_label)
        if transits is None:
            if not get_birth_chart_for_user(g.user_id, person_label):
                return jsonify({'error': 'No hay carta natal guardada para esa persona'}), 404
            return jsonify({'error': 'Fecha inválida. Usa el formato YYYY-MM-DD.'}), 400
    else:
        transits = get_transits_for_user(g.user_id, person_label)
        if transits is None:
            return jsonify({'error': 'No hay carta natal guardada para esa persona'}), 404

    return jsonify(transits)


@astrology_bp.route('/astrology/birth-chart/svg', methods=['GET'])
@require_auth
def get_birth_chart_svg():
    """
    Devuelve el SVG de una carta natal guardada.
    Query param opcional: ?person=Marco (hijo)
    """
    person_label = request.args.get('person', DEFAULT_PERSON_LABEL).strip() or DEFAULT_PERSON_LABEL
    svg = get_birth_chart_svg_for_user(g.user_id, person_label)
    if svg is None:
        return jsonify({'error': 'No hay carta natal guardada para esa persona'}), 404
    return Response(svg, mimetype='image/svg+xml')


@astrology_bp.route('/astrology/transits/svg', methods=['GET'])
@require_auth
def get_transit_chart_svg():
    """
    Devuelve el SVG de la carta bi-wheel (natal + tránsitos).
    Query params opcionales: ?date=YYYY-MM-DD, ?person=Marco (hijo)
    """
    target_date  = request.args.get('date', '').strip() or None
    person_label = request.args.get('person', DEFAULT_PERSON_LABEL).strip() or DEFAULT_PERSON_LABEL
    svg = get_transit_chart_svg_for_user(g.user_id, target_date, person_label)
    if svg is None:
        if not get_birth_chart_for_user(g.user_id, person_label):
            return jsonify({'error': 'No hay carta natal guardada para esa persona'}), 404
        return jsonify({'error': 'Fecha inválida. Usa el formato YYYY-MM-DD.'}), 400
    return Response(svg, mimetype='image/svg+xml')


@astrology_bp.route('/astrology/interpret', methods=['POST'])
@require_auth
def interpret_astrology():
    """
    Interpreta la carta/tránsitos usando el modelo dedicado de astrología
    (más riguroso, más económico) en vez del chat general.

    Body esperado: {
        "message": "¿qué significa mi Quirón en Cáncer?",
        "date": "2027-06-15",       // opcional
        "person_label": "Marco"     // opcional, por defecto "yo"
    }
    """
    data         = request.get_json() or {}
    message      = (data.get('message') or '').strip()
    person_label = (data.get('person_label') or DEFAULT_PERSON_LABEL).strip() or DEFAULT_PERSON_LABEL
    if not message:
        return jsonify({'error': 'Falta el campo message'}), 400

    target_date = (data.get('date') or '').strip() or extract_date_from_message(message)
    astrology_context = format_astrology_context(g.user_id, target_date, person_label)

    if not astrology_context:
        return jsonify({'error': 'No hay carta natal guardada para esa persona'}), 404

    try:
        response_text = call_groq_astrology(message, astrology_context)
    except GroqRateLimitError as e:
        logger.error(f'[ASTROLOGY] Rate limit: {e}')
        minutos = max(1, round(e.retry_after_seconds / 60)) if e.retry_after_seconds else None
        mensaje = (f'El modelo de astrología necesita descansar — inténtalo en unos {minutos} minutos.'
                  if minutos else 'El modelo de astrología necesita descansar — inténtalo de nuevo en unos minutos.')
        return jsonify({'error': mensaje}), 429
    except Exception as e:
        logger.error(f'[ASTROLOGY] Error: {e}')
        return jsonify({'error': 'Error al interpretar la carta'}), 500

    return jsonify({'text': response_text})
