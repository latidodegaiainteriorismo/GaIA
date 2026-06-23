import logging
from flask import Blueprint, jsonify, request, g
from db import db_all, db_one, db_run
from auth import require_auth

logger           = logging.getLogger(__name__)
conversations_bp = Blueprint('conversations', __name__)

@conversations_bp.route('/conversations', methods=['GET'])
@require_auth
def list_conversations():
    convs = db_all(
        "SELECT id, title, created_at, updated_at FROM conversations "
        "WHERE user_id = %s ORDER BY updated_at DESC LIMIT 50",
        (g.user_id,)
    )
    for c in convs:
        c['id']         = str(c['id'])
        c['created_at'] = c['created_at'].isoformat() if c['created_at'] else None
        c['updated_at'] = c['updated_at'].isoformat() if c['updated_at'] else None
    return jsonify(convs)

@conversations_bp.route('/conversations', methods=['POST'])
@require_auth
def create_conversation():
    data  = request.get_json() or {}
    title = data.get('title', 'Nueva conversación')
    conv  = db_one(
        "INSERT INTO conversations (user_id, title) VALUES (%s, %s) RETURNING id, title",
        (g.user_id, title)
    )
    if conv:
        conv['id'] = str(conv['id'])
    return jsonify(conv), 201

@conversations_bp.route('/conversations/<conv_id>', methods=['DELETE'])
@require_auth
def delete_conversation(conv_id):
    db_run(
        "DELETE FROM conversations WHERE id = %s AND user_id = %s",
        (conv_id, g.user_id)
    )
    return jsonify({'status': 'ok'})

@conversations_bp.route('/conversations/<conv_id>/messages', methods=['GET'])
@require_auth
def get_messages(conv_id):
    msgs = db_all(
        "SELECT role, content FROM messages "
        "WHERE conversation_id = %s ORDER BY created_at ASC LIMIT 50",
        (conv_id,)
    )
    return jsonify(msgs)
