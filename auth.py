import secrets
import logging
from functools import wraps
from flask import request, jsonify, g
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from db import db_one, db_run
from config import GOOGLE_CLIENT_ID, DEVELOPER_EMAIL

logger = logging.getLogger(__name__)

def get_user_from_token(token: str):
    """Valida el token de sesión y devuelve user_id o None."""
    if not token:
        return None
    row = db_one(
        "SELECT user_id FROM sessions WHERE token = %s AND expires_at > NOW()",
        (token,)
    )
    return str(row['user_id']) if row else None

def get_user_email(user_id: str) -> str | None:
    """Devuelve el email del usuario, o None si no existe."""
    if not user_id:
        return None
    row = db_one("SELECT email FROM users WHERE id = %s", (user_id,))
    return row['email'] if row else None

def is_developer(user_id: str) -> bool:
    """
    Comprueba si el usuario autenticado es el desarrollador de GaIA
    (identificado por su email de Google, ver config.DEVELOPER_EMAIL).
    Usado para habilitar comandos especiales como editar el ADN.
    """
    email = get_user_email(user_id)
    return bool(email) and email.lower() == DEVELOPER_EMAIL.lower()

def require_auth(f):
    """Decorador: requiere sesión válida. Inyecta g.user_id."""
    @wraps(f)
    def decorated(*args, **kwargs):
        token   = request.headers.get('Authorization', '').replace('Bearer ', '')
        user_id = get_user_from_token(token)
        if not user_id:
            return jsonify({'error': 'No autorizado'}), 401
        g.user_id = user_id
        return f(*args, **kwargs)
    return decorated

def require_developer(f):
    """Decorador: requiere sesión válida Y ser el desarrollador. Inyecta g.user_id."""
    @wraps(f)
    def decorated(*args, **kwargs):
        token   = request.headers.get('Authorization', '').replace('Bearer ', '')
        user_id = get_user_from_token(token)
        if not user_id:
            return jsonify({'error': 'No autorizado'}), 401
        if not is_developer(user_id):
            return jsonify({'error': 'Acción restringida al desarrollador'}), 403
        g.user_id = user_id
        return f(*args, **kwargs)
    return decorated

def verify_google_token(credential: str) -> dict | None:
    """Verifica el token de Google y devuelve el payload o None si es inválido."""
    try:
        return id_token.verify_oauth2_token(
            credential,
            google_requests.Request(),
            GOOGLE_CLIENT_ID
        )
    except ValueError as e:
        logger.error(f'[Auth] Token Google inválido: {e}')
        return None

def create_session(user_id: str) -> str:
    """Crea una nueva sesión y devuelve el token."""
    token = secrets.token_urlsafe(32)
    db_run(
        "INSERT INTO sessions (user_id, token) VALUES (%s, %s)",
        (user_id, token)
    )
    return token
