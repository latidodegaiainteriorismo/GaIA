import logging
from flask import Blueprint, request, jsonify
from db import db_all, db_one, db_run
from auth import get_user_from_token
from llm import call_groq
from tts import text_to_speech
from knowledge import search_knowledge, format_knowledge_context

logger  = logging.getLogger(__name__)
chat_bp = Blueprint('chat', __name__)

# ── Helpers de memoria (TEMPORAL — FASE 1: mover a context.py) ────────────────

def _get_conv_messages(conv_id: str, limit: int = 50) -> list:
    return db_all(
        "SELECT role, content FROM messages "
        "WHERE conversation_id = %s ORDER BY created_at ASC LIMIT %s",
        (conv_id, limit)
    )

def _get_cross_memory(user_id: str, current_conv_id: str = None,
                      max_convs: int = 3, msgs_per: int = 4) -> str:
    """
    Inyecta los últimos mensajes de conversaciones anteriores como contexto.
    TEMPORAL: En FASE 1 será reemplazado por context.py con RAG semántico
    y perfil vivo del usuario.
    """
    null_id = '00000000-0000-0000-0000-000000000000'
    convs   = db_all(
        "SELECT id, title FROM conversations "
        "WHERE user_id = %s AND id != %s ORDER BY updated_at DESC LIMIT %s",
        (user_id, current_conv_id or null_id, max_convs)
    )
    if not convs:
        return ''

    parts = []
    for c in convs:
        msgs = db_all(
            "SELECT role, content FROM messages "
            "WHERE conversation_id = %s ORDER BY created_at ASC LIMIT %s",
            (str(c['id']), msgs_per)
        )
        if not msgs:
            continue
        block = f"[Conversación anterior: \"{c.get('title', '...')}\"]"
        for m in msgs:
            speaker = 'Usuario' if m['role'] == 'user' else 'GaIA'
            block  += f"\n{speaker}: {m['content']}"
        parts.append(block)

    if not parts:
        return ''
    return '\n\n## MEMORIA DE CONVERSACIONES ANTERIORES\n' + '\n\n'.join(parts) + '\n---\n'

# ── Ruta principal ─────────────────────────────────────────────────────────────

@chat_bp.route('/chat', methods=['POST'])
def chat():
    data    = request.get_json() or {}
    message = (data.get('message') or '').strip()
    if not message:
        return jsonify({'error': 'Mensaje vacío'}), 400

    voice_mode = data.get('voice_mode', True)
    temporary  = data.get('temporary', False)
    conv_id    = data.get('conversation_id')
    token      = request.headers.get('Authorization', '').replace('Bearer ', '')
    user_id    = get_user_from_token(token) if token else None

    logger.info(f'[CHAT] user={user_id} conv={conv_id} voice={voice_mode} len={len(message)}')

    # ── Modo temporal (sin guardar) ──────────────────────────────────────────
    if temporary:
        history      = data.get('history', [])
        history.append({'role': 'user', 'content': message})
        cross_memory = ''

    # ── Modo normal (autenticado, persistente) ───────────────────────────────
    else:
        if not user_id:
            return jsonify({'error': 'No autorizado'}), 401

        if not conv_id:
            conv    = db_one(
                "INSERT INTO conversations (user_id, title) VALUES (%s, %s) RETURNING id",
                (user_id, message[:60])
            )
            conv_id = str(conv['id']) if conv else None

        history      = _get_conv_messages(conv_id) if conv_id else []
        cross_memory = _get_cross_memory(user_id, conv_id)
        history.append({'role': 'user', 'content': message})

        if conv_id:
            db_run(
                "INSERT INTO messages (conversation_id, user_id, role, content) "
                "VALUES (%s, %s, %s, %s)",
                (conv_id, user_id, 'user', message)
            )
            db_run("UPDATE conversations SET updated_at = NOW() WHERE id = %s", (conv_id,))

    # ── Knowledge RAG (FASE 2) ───────────────────────────────────────────────
    chunks           = search_knowledge(message, top_k=3)
    knowledge_context = format_knowledge_context(chunks)

    # ── Llamada al LLM ───────────────────────────────────────────────────────
    try:
        gaia_text = call_groq(history, cross_memory, knowledge_context=knowledge_context)
    except Exception as e:
        logger.error(f'[CHAT] Groq error: {e}')
        return jsonify({'error': 'Error al conectar con GaIA'}), 500

    # ── Guardar respuesta ────────────────────────────────────────────────────
    if not temporary and conv_id:
        db_run(
            "INSERT INTO messages (conversation_id, user_id, role, content) "
            "VALUES (%s, %s, %s, %s)",
            (conv_id, user_id, 'assistant', gaia_text)
        )

    # ── TTS ──────────────────────────────────────────────────────────────────
    audio_b64 = None
    if voice_mode:
        audio_b64 = text_to_speech(gaia_text)
        if not audio_b64:
            logger.warning('[CHAT] TTS falló — respuesta sin audio')

    resp = {'text': gaia_text, 'audio': audio_b64}
    if conv_id:
        resp['conversation_id'] = conv_id
    return jsonify(resp)
