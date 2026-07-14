import json
import logging
from flask import Blueprint, request, jsonify
from db import db_all, db_one, db_run
from auth import get_user_from_token, is_developer
from llm import call_groq, GroqRateLimitError
from tts import text_to_speech
from knowledge import search_knowledge, format_knowledge_context
from astrology import (
    format_astrology_context, extract_date_from_message, extract_person_from_message,
    extract_new_chart_request, create_birth_chart_for_user, is_astrology_related,
)
from dev_commands import parse_dev_command, execute_dev_command
from user_profile import (
    needs_onboarding, mark_onboarding_completed, format_profile_context,
    format_onboarding_prompt_instruction, detect_new_personal_data,
    format_new_data_detected_instruction, extract_and_apply_save_marks,
)

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

# ── Fuentes reales por mensaje — para que GaIA pueda ser honesta cuando le ──
# preguntan "¿en qué te basas?" en vez de improvisar una cita plausible ──────

_SOURCE_QUESTION_TRIGGERS = [
    "en qué documento", "en que documento", "en qué documentos", "en que documentos",
    "en qué te basas", "en que te basas", "en qué se basa", "en que se basa",
    "de dónde sacas", "de donde sacas", "de dónde sale eso", "de donde sale eso",
    "de dónde salió eso", "de donde salio eso",
    "cuál es tu fuente", "cual es tu fuente", "cuáles son tus fuentes", "cuales son tus fuentes",
    "qué fuente", "que fuente", "qué fuentes", "que fuentes",
    "en qué basas", "en que basas", "en qué te basaste", "en que te basaste",
    "de qué documento", "de que documento", "según qué documento", "segun que documento",
    "qué documento usaste", "que documento usaste",
]


def _is_source_question(message: str) -> bool:
    """
    Detecta si el usuario pregunta por las fuentes/documentos usados en la
    respuesta ANTERIOR de GaIA — para tratar este turno de forma especial
    (ver _format_sources_question_block) en vez de lanzar una búsqueda RAG
    nueva sobre el texto literal de esta pregunta-meta, que nunca encontrará
    nada relevante y puede llevar a GaIA a rellenar el hueco con una cita
    inventada.
    """
    msg_lower = message.lower()
    return any(t in msg_lower for t in _SOURCE_QUESTION_TRIGGERS)


def _get_last_assistant_sources(conv_id: str) -> list:
    """
    Recupera las fuentes reales guardadas del último mensaje de GaIA en esta
    conversación (columna messages.sources, ver migración SQL). Devuelve
    lista vacía si no hay mensaje previo, o si ese mensaje no tiene fuentes
    guardadas (NULL — por ejemplo, mensajes anteriores a esta funcionalidad).
    """
    row = db_one(
        "SELECT sources FROM messages WHERE conversation_id = %s AND role = 'assistant' "
        "ORDER BY created_at DESC LIMIT 1",
        (conv_id,)
    )
    if not row or not row.get('sources'):
        return []
    sources = row['sources']
    if isinstance(sources, str):
        try:
            sources = json.loads(sources)
        except Exception:
            return []
    return sources or []


def _format_sources_question_block(sources: list) -> str:
    """
    Construye la instrucción que reemplaza al RAG normal cuando el usuario
    pregunta por las fuentes de la respuesta anterior. Le da a GaIA la lista
    REAL (verificada por el sistema) en vez de dejar que adivine o invente.
    """
    if sources:
        lista = "\n".join(f"- {s}" for s in sources)
        return (
            "\n## PREGUNTA SOBRE TUS FUENTES REALES\n"
            "El usuario te pregunta en qué te basaste para tu respuesta anterior. "
            "Estas son las fuentes REALES que el sistema usó para construir esa "
            f"respuesta (verificadas, no las cambies ni inventes otras):\n{lista}\n"
            "Cita únicamente estas fuentes, con naturalidad. No menciones ningún "
            "documento que no esté en esta lista.\n"
        )
    return (
        "\n## PREGUNTA SOBRE TUS FUENTES REALES\n"
        "El usuario te pregunta en qué te basaste para tu respuesta anterior. El "
        "sistema confirma que esa respuesta NO usó ningún documento concreto de tu "
        "Knowledge — se respondió desde tu comprensión general. Dilo con "
        "naturalidad y honestidad; no inventes ni menciones ningún documento como "
        "si lo hubieras usado.\n"
    )


def _extract_sources_used(chunks: list, web_context: str) -> list:
    """Nombres de documento únicos realmente usados en este turno, para guardar."""
    sources = sorted({
        c.get('source', '').split('/')[-1].replace('.txt', '')
        for c in chunks if c.get('source')
    })
    if web_context:
        sources.append('Búsqueda web')
    return sources

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

    # ── Modo desarrollador — comandos especiales de configuración ───────────
    user_is_developer = bool(user_id) and is_developer(user_id)

    if user_is_developer:
        dev_command = parse_dev_command(message)
        if dev_command:
            action, payload = dev_command
            logger.info(f'[CHAT] Comando de desarrollador: {action}')
            dev_response = execute_dev_command(action, payload)

            if not temporary and user_id:
                if not conv_id:
                    conv    = db_one(
                        "INSERT INTO conversations (user_id, title) VALUES (%s, %s) RETURNING id",
                        (user_id, message[:60])
                    )
                    conv_id = str(conv['id']) if conv else None
                if conv_id:
                    db_run(
                        "INSERT INTO messages (conversation_id, user_id, role, content) VALUES (%s, %s, %s, %s)",
                        (conv_id, user_id, 'user', message)
                    )
                    db_run(
                        "INSERT INTO messages (conversation_id, user_id, role, content) VALUES (%s, %s, %s, %s)",
                        (conv_id, user_id, 'assistant', dev_response)
                    )

            resp = {'text': dev_response, 'audio': None}
            if conv_id:
                resp['conversation_id'] = conv_id
            return jsonify(resp)

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

    # ── ¿Pregunta por las fuentes de la respuesta anterior? ──────────────────
    # Si es así, saltamos el RAG normal por completo para este turno — una
    # búsqueda nueva sobre el texto de esta pregunta-meta nunca encontraría
    # nada relevante, y dejaría a GaIA rellenando el hueco con una cita
    # inventada. En su lugar, le damos la lista REAL de fuentes guardadas
    # del mensaje anterior (o le confirmamos que no hubo ninguna).
    is_source_question = _is_source_question(message)

    if is_source_question and not temporary and conv_id:
        chunks, web_context = [], ''
        knowledge_context = ''
        previous_sources = _get_last_assistant_sources(conv_id)
        sources_question_block = _format_sources_question_block(previous_sources)
    else:
        # ── Knowledge RAG v6.1 — Mónica siempre + routing por documento + web
        chunks, web_context = search_knowledge(message)
        knowledge_context   = format_knowledge_context(chunks, web_context)
        sources_question_block = ''

    # ── Astrología — carta natal + tránsitos del usuario, si los tiene ──────
    astrology_context = ''
    new_chart_suffix = ''
    if user_id:
        new_chart_request = extract_new_chart_request(message)
        if new_chart_request:
            created = create_birth_chart_for_user(
                user_id,
                new_chart_request["birth_date"],
                new_chart_request["birth_time"],
                new_chart_request["birth_place"],
                new_chart_request["person_label"],
                new_chart_request["relationship"],
            )
            if created:
                new_chart_suffix = (
                    f"\n## CARTA NUEVA CALCULADA\nAcabas de calcular y guardar la carta natal de "
                    f"{new_chart_request['person_label']}. Confírmaselo al usuario con calidez, "
                    f"mencionando el Sol ({created['planets'][0]['sign']}) si aporta.\n"
                )
            else:
                new_chart_suffix = (
                    "\n## CARTA NUEVA — ERROR\nEl usuario pidió calcular una carta pero no se "
                    "pudo procesar el lugar de nacimiento. Explícaselo con calidez y pídele que "
                    "lo intente de nuevo, quizá siendo más específico con la ciudad y el país.\n"
                )

        if new_chart_request or is_astrology_related(message):
            target_date = extract_date_from_message(message)
            person_label = extract_person_from_message(user_id, message)
            astrology_context = format_astrology_context(user_id, target_date, person_label)

    # ── Perfil de usuario — onboarding único + detección de datos nuevos ────
    profile_context     = ''
    onboarding_suffix    = ''
    new_data_suffix      = ''
    if user_id and not temporary:
        profile_context = format_profile_context(user_id)

        if needs_onboarding(user_id):
            onboarding_suffix = format_onboarding_prompt_instruction()
            mark_onboarding_completed(user_id)
        else:
            detected = detect_new_personal_data(user_id, message)
            if detected:
                new_data_suffix = format_new_data_detected_instruction(detected)

    # ── Llamada al LLM ───────────────────────────────────────────────────────
    developer_prefix = ''
    if user_is_developer:
        developer_prefix = (
            "## QUIÉN TE HABLA AHORA MISMO\n"
            "La persona con la que hablas es Adrián Lozano, tu desarrollador — quien te ha "
            "construido y te mantiene. Puedes reconocerlo con naturalidad si viene a cuento "
            "(por ejemplo, si te pregunta quién lo creó a él o quién te hizo a ti), pero no "
            "lo repitas de forma forzada en cada respuesta. Sabe que puede pedirte cambios en "
            "tu ADN directamente por chat.\n\n"
        )

    extra_prefix = (developer_prefix + profile_context + onboarding_suffix +
                    new_data_suffix + new_chart_suffix + sources_question_block)

    try:
        gaia_text = call_groq(history, cross_memory, knowledge_context=knowledge_context,
                              astrology_context=astrology_context, extra_system_prefix=extra_prefix)
    except GroqRateLimitError as e:
        logger.error(f'[CHAT] Rate limit: {e}')
        if e.retry_after_seconds:
            minutos = max(1, round(e.retry_after_seconds / 60))
            mensaje = f'GaIA necesita descansar un poco — vuelve a intentarlo en unos {minutos} minutos.'
        else:
            mensaje = 'GaIA necesita descansar un poco — inténtalo de nuevo en unos minutos.'
        return jsonify({'error': mensaje}), 429
    except Exception as e:
        logger.error(f'[CHAT] Groq error: {e}')
        return jsonify({'error': 'Error al conectar con GaIA'}), 500

    # ── Perfil: aplicar marcas [GUARDAR_PERFIL: ...] si las hay, y limpiarlas ──
    if user_id and not temporary:
        gaia_text = extract_and_apply_save_marks(user_id, gaia_text)

    # ── Guardar respuesta (con las fuentes reales usadas en este turno) ─────
    if not temporary and conv_id:
        sources_used = _extract_sources_used(chunks, web_context)
        db_run(
            "INSERT INTO messages (conversation_id, user_id, role, content, sources) "
            "VALUES (%s, %s, %s, %s, %s)",
            (conv_id, user_id, 'assistant', gaia_text, json.dumps(sources_used))
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
