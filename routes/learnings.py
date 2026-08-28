"""
routes/learnings.py

Gestión de lo que classification.py va extrayendo de las conversaciones
del creator: notas de Estructura Basal (activar/desactivar = revertir) y
conocimiento derivado de conversación (Enciclopedia/Astrología viva),
promovible a visibilidad pública igual que el sistema de audios.

Todos los endpoints requieren rol creator.
"""

import logging
from flask import Blueprint, request, jsonify, g
from db import db_all, db_one, db_run
from auth import require_creator

logger = logging.getLogger(__name__)
learnings_bp = Blueprint('learnings', __name__)


# ── Estructura Basal ──────────────────────────────────────────────────────────

@learnings_bp.route('/learnings/basal', methods=['GET'])
@require_creator
def list_basal_notes():
    """Lista todas las notas de Estructura Basal, activas e inactivas."""
    rows = db_all(
        "SELECT id, content, source_excerpt, active, created_at "
        "FROM basal_notes ORDER BY created_at DESC"
    )
    return jsonify({'notes': rows or []})


@learnings_bp.route('/learnings/basal/<note_id>/toggle', methods=['PATCH'])
@require_creator
def toggle_basal_note(note_id: str):
    """
    Activa o desactiva una nota de Estructura Basal. Desactivarla la
    excluye INMEDIATAMENTE de la próxima respuesta de GaIA (a cualquier
    usuario) — es el mecanismo de "revertir" un aprendizaje.
    """
    data   = request.get_json() or {}
    active = data.get('active')
    if active is None:
        return jsonify({'error': "Falta el campo 'active' (true/false)"}), 400

    ok = db_run("UPDATE basal_notes SET active = %s WHERE id = %s", (bool(active), note_id))
    if not ok:
        return jsonify({'error': 'Nota no encontrada'}), 404

    logger.info(f"[learnings] Nota basal {note_id} -> active={active}")
    return jsonify({'id': note_id, 'active': bool(active)})


@learnings_bp.route('/learnings/basal/<note_id>', methods=['DELETE'])
@require_creator
def delete_basal_note(note_id: str):
    """Elimina definitivamente una nota de Estructura Basal (no solo desactivar)."""
    ok = db_run("DELETE FROM basal_notes WHERE id = %s", (note_id,))
    if not ok:
        return jsonify({'error': 'Nota no encontrada'}), 404
    return jsonify({'id': note_id, 'deleted': True})


# ── Conocimiento derivado de conversación (Enciclopedia / Astrología) ────────

@learnings_bp.route('/learnings/knowledge', methods=['GET'])
@require_creator
def list_conversation_knowledge():
    """
    Lista el conocimiento derivado de conversación viva (source =
    'Conversación viva'), tanto privado como ya promovido, para que el
    creator pueda revisarlo y decidir qué promover a 'all'.
    """
    category = request.args.get('category')  # filtro opcional
    if category:
        rows = db_all(
            "SELECT id, content, category, visibility, created_at "
            "FROM knowledge_chunks WHERE source = 'Conversación viva' AND category = %s "
            "ORDER BY created_at DESC",
            (category,)
        )
    else:
        rows = db_all(
            "SELECT id, content, category, visibility, created_at "
            "FROM knowledge_chunks WHERE source = 'Conversación viva' "
            "ORDER BY created_at DESC"
        )
    return jsonify({'items': rows or []})


@learnings_bp.route('/learnings/knowledge/<item_id>/promote', methods=['PATCH'])
@require_creator
def promote_conversation_knowledge(item_id: str):
    """
    Cambia la visibilidad de un item de conocimiento derivado de
    conversación entre 'creator' (privado) y 'all' (público para
    cualquier usuario) — mismo patrón que la promoción de audios.
    """
    data       = request.get_json() or {}
    visibility = data.get('visibility', 'all')
    if visibility not in ('creator', 'all'):
        return jsonify({'error': "visibility debe ser 'creator' o 'all'"}), 400

    ok = db_run(
        "UPDATE knowledge_chunks SET visibility = %s "
        "WHERE id = %s AND source = 'Conversación viva'",
        (visibility, item_id)
    )
    if not ok:
        return jsonify({'error': 'Item no encontrado'}), 404

    logger.info(f"[learnings] Conocimiento {item_id} -> visibility={visibility}")
    return jsonify({'id': item_id, 'visibility': visibility})


@learnings_bp.route('/learnings/knowledge/<item_id>', methods=['DELETE'])
@require_creator
def delete_conversation_knowledge(item_id: str):
    """Elimina definitivamente un item de conocimiento derivado de conversación."""
    ok = db_run(
        "DELETE FROM knowledge_chunks WHERE id = %s AND source = 'Conversación viva'",
        (item_id,)
    )
    if not ok:
        return jsonify({'error': 'Item no encontrado'}), 404
    return jsonify({'id': item_id, 'deleted': True})
