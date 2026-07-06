import logging
from flask import Blueprint, jsonify, request
from auth import require_developer
from llm import load_dna, save_dna, load_dna_astrologia, save_dna_astrologia

logger    = logging.getLogger(__name__)
dev_bp    = Blueprint('dev', __name__)


@dev_bp.route('/dev/dna', methods=['GET'])
@require_developer
def get_dna():
    """Devuelve el ADN general actual de GaIA. Solo el desarrollador."""
    return jsonify({'content': load_dna()})


@dev_bp.route('/dev/dna', methods=['PUT'])
@require_developer
def update_dna():
    """
    Sustituye el ADN general completo de GaIA.
    Body: { "content": "..." }
    """
    data    = request.get_json() or {}
    content = data.get('content')
    if content is None:
        return jsonify({'error': 'Falta el campo content'}), 400
    ok = save_dna(content)
    if not ok:
        return jsonify({'error': 'No se pudo guardar el ADN'}), 500
    logger.info('[DEV] ADN general actualizado vía endpoint')
    return jsonify({'status': 'ok', 'length': len(content)})


@dev_bp.route('/dev/dna/astrologia', methods=['GET'])
@require_developer
def get_dna_astrologia():
    """Devuelve el ADN de astrología actual. Solo el desarrollador."""
    return jsonify({'content': load_dna_astrologia()})


@dev_bp.route('/dev/dna/astrologia', methods=['PUT'])
@require_developer
def update_dna_astrologia():
    """
    Sustituye el ADN de astrología completo.
    Body: { "content": "..." }
    """
    data    = request.get_json() or {}
    content = data.get('content')
    if content is None:
        return jsonify({'error': 'Falta el campo content'}), 400
    ok = save_dna_astrologia(content)
    if not ok:
        return jsonify({'error': 'No se pudo guardar el ADN de astrología'}), 500
    logger.info('[DEV] ADN de astrología actualizado vía endpoint')
    return jsonify({'status': 'ok', 'length': len(content)})
