import logging
from flask import Blueprint, request, jsonify, g
from db import db_one
from auth import verify_google_token, create_session, require_auth

logger  = logging.getLogger(__name__)
auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/auth/google', methods=['POST'])
def auth_google():
    data       = request.get_json() or {}
    credential = data.get('credential')
    if not credential:
        return jsonify({'error': 'Token requerido'}), 400

    payload = verify_google_token(credential)
    if not payload:
        return jsonify({'error': 'Token de Google inválido'}), 401

    google_id = payload['sub']
    email     = payload.get('email', '')
    username  = payload.get('name') or email.split('@')[0]

    user = db_one("SELECT id, username FROM users WHERE google_id = %s", (google_id,))
    if not user:
        user = db_one(
            "INSERT INTO users (username, email, google_id) VALUES (%s, %s, %s) RETURNING id, username",
            (username, email, google_id)
        )
    if not user:
        return jsonify({'error': 'Error al crear usuario'}), 500

    token = create_session(str(user['id']))
    logger.info(f'[Auth] Login OK: {email}')
    return jsonify({'token': token, 'username': user['username']})

@auth_bp.route('/auth/logout', methods=['POST'])
@require_auth
def logout():
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    from db import db_run
    db_run("DELETE FROM sessions WHERE token = %s", (token,))
    logger.info(f'[Auth] Logout: user={g.user_id}')
    return jsonify({'status': 'ok'})
