import json
import logging
from flask import Blueprint, request, jsonify
from db import db_all, db_one, db_run
from auth import get_user_from_token, is_developer, is_creator
from llm import call_groq, GroqRateLimitError
from tts import text_to_speech
from knowledge import (search_knowledge, format_knowledge_context,
                       search_knowledge_creator, format_knowledge_context_creator)
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
from synthesis import format_synthesis_context
from memory_search import search_user_memory, format_deep_memory

logger  = logging.getLogger(__name__)
chat_bp = Blueprint('chat', __name__)

# ── Presupuesto de contexto (FASE 0) ──────────────────────────────────────────
#
# MIGRACIÓN (19-ago-2026): tras pasar de Groq (8K TPM) a Gemini 3.5
# Flash-Lite (~1M de contexto, ver config.py), el límite de 2.600 tokens
# quedó obsoleto — era una restricción impuesta por el TPM estrecho de
# Groq, no por ninguna necesidad real de GaIA. Subido a 20.000: cubre con
# holgura el peor caso real visto en producción (11.402 tokens estimados,
# con el router activando 5 documentos del catálogo a la vez + memoria
# profunda + histórico cruzado), dejando margen para preguntas aún más
# exigentes sin gastar de más innecesariamente en cada llamada.
#
# El orden de recorte y la lógica de truncado (en vez de descarte total)
# de cross_memory se conservan sin cambios — siguen siendo la salvaguarda
# correcta para el caso extremo en que, pese al presupuesto mucho mayor,
# el contexto combinado lo siga superando.
#
# CORRECCIÓN (19-ago-2026, previa a la migración): la intención original
# era que la memoria profunda (FASE 2) nunca se descartara por ser la
# pieza de mayor impacto — pero como se concatena DENTRO de cross_memory
# (ver más abajo, `cross_memory = cross_memory + deep_memory`), y
# cross_memory estaba PRIMERO en el orden de recorte, en la práctica la
# memoria profunda era justo lo primero que se tiraba en cualquier
# pregunta con contexto grande. Esto causó un bug real: una pregunta de
# resumen conceptual trajo memoria episódica de un tema personal no
# relacionado, y al no caber el resto del contexto, knowledge_context
# sobrevivió mientras cross_memory (con sus instrucciones de "no fuerces
# la conexión si no encaja") se perdió.
#
# Orden: cross_memory (incluida la memoria profunda) se recorta EN ÚLTIMO
# lugar. knowledge y astrology se descartan antes por ser más reemplazables
# por el conocimiento general del modelo; la memoria de usuario no tiene
# sustituto.

_CHARS_POR_TOKEN    = 3.6
_MAX_CONTEXT_TOKENS = 20000
_ORDEN_DE_RECORTE   = ['astrology', 'knowledge', 'cross_memory']
_MSGS_CONVERSACION_ACTIVA = 30


def _estimar_tokens(texto: str) -> int:
    if not texto:
        return 0
    return int(len(texto) / _CHARS_POR_TOKEN)


def _ajustar_a_presupuesto(bloques: dict) -> dict:
    total = sum(_estimar_tokens(v) for v in bloques.values())
    if total <= _MAX_CONTEXT_TOKENS:
        return bloques

    logger.info(f'[contexto] {total} tokens estimados > {_MAX_CONTEXT_TOKENS} — recortando')

    for clave in _ORDEN_DE_RECORTE:
        if total <= _MAX_CONTEXT_TOKENS:
            break
        if not bloques.get(clave):
            continue

        tokens_bloque = _estimar_tokens(bloques[clave])

        # cross_memory nunca se vacía del todo si por sí solo cabe en el
        # presupuesto entero: se trunca por caracteres en vez de
        # descartarse, conservando el principio del bloque. format_deep_memory
        # pone la memoria profunda (FASE 2, más relevante semánticamente)
        # ANTES que el histórico de conversaciones cruzadas — truncar por el
        # final prioriza correctamente lo más valioso.
        if clave == 'cross_memory' and tokens_bloque > _MAX_CONTEXT_TOKENS:
            max_chars = int(_MAX_CONTEXT_TOKENS * _CHARS_POR_TOKEN)
            bloques[clave] = bloques[clave][:max_chars] + '\n[...memoria truncada por espacio...]\n'
            liberados = tokens_bloque - _MAX_CONTEXT_TOKENS
            total -= liberados
            logger.info(f'[contexto] Truncado "{clave}" a ~{_MAX_CONTEXT_TOKENS} tokens '
                        f'(-{liberados} tokens) → {total}')
        else:
            bloques[clave] = ''
            total -= tokens_bloque
            logger.info(f'[contexto] Descartado "{clave}" (-{tokens_bloque} tokens) → {total}')

    if total > _MAX_CONTEXT_TOKENS:
        logger.warning(f'[contexto] Sigue por encima del presupuesto ({total}) tras recortar todo')

    return bloques


# ── Helpers de memoria ────────────────────────────────────────────────────────

def _get_conv_messages(conv_id: str, limit: int = _MSGS_CONVERSACION_ACTIVA) -> list:
    """
    Carga los mensajes MÁS RECIENTES de la conversación activa.
    FASE 0 fix: ORDER BY DESC + reversed para no perder los mensajes
    más recientes en conversaciones largas.
    """
    rows = db_all(
        "SELECT role, content FROM messages "
        "WHERE conversation_id = %s ORDER BY created_at DESC LIMIT %s",
        (conv_id, limit)
    )
    return list(reversed(rows or []))


def _get_cross_memory(user_id: str, current_conv_id: str = None,
                      max_convs: int = 2, msgs_per: int = 3) -> str:
    """
    Inyecta fragmentos de conversaciones anteriores como contexto literal.
    Complementa a la memoria profunda (FASE 2): esta trae mensajes recientes
    sin filtrar; la profunda trae los más relevantes por significado.

    AJUSTE (19-ago-2026): bajado de max_convs=3/msgs_per=4 a 2/3. Este bloque
    va SIN filtrar por relevancia (a diferencia de deep_memory, que sí filtra
    por significado) — es "lo último que se habló", no "lo más relevante para
    esta pregunta". Con los valores anteriores, sumado a deep_memory, el
    bloque combinado (cross_memory) podía superar por sí solo el presupuesto
    total del contexto (_MAX_CONTEXT_TOKENS), forzando un truncado que corta
    a ciegas. Menos volumen sin filtrar deja más margen relativo a la parte
    que sí está filtrada por relevancia.
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


# ── Fuentes reales por mensaje ────────────────────────────────────────────────

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

_CONTENT_REQUEST_CUES = [
    "háblame de", "hablame de", "cuéntame de", "cuentame de", "cuéntame sobre",
    "cuentame sobre", "explícame", "explicame", "información sobre",
    "informacion sobre", "sabes de", "sabes sobre", "dime sobre", "qué es",
    "que es", "qué sabes de", "que sabes de", "enséñame", "ensename",
]


def _is_source_question(message: str) -> bool:
    msg_lower = message.lower()
    return any(t in msg_lower for t in _SOURCE_QUESTION_TRIGGERS)


def _is_pure_source_question(message: str) -> bool:
    if not _is_source_question(message):
        return False
    msg_lower = message.lower()
    if any(cue in msg_lower for cue in _CONTENT_REQUEST_CUES):
        return False
    return len(message.strip()) <= 90


def _get_last_assistant_sources(conv_id: str) -> list:
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


def _format_combined_sources_reminder() -> str:
    return (
        "\n## RECORDATORIO — CITA SOLO FUENTES REALES DE ESTE TURNO\n"
        "El usuario también te pide que confirmes en qué documentos te basas. "
        "Cita ÚNICAMENTE los documentos que aparezcan en el bloque 'CONTEXTO "
        "RECUPERADO DE TU KNOWLEDGE' de este mismo turno. Si ese bloque viene "
        "vacío o no trae nada relevante para esta pregunta concreta, dilo con "
        "honestidad — NUNCA menciones un documento por nombre si no aparece "
        "ahí, aunque sea un documento real que sí conozcas.\n"
    )


def _extract_sources_used(chunks: list, web_context: str) -> list:
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

    # ── Modo desarrollador ───────────────────────────────────────────────────
    user_is_developer = bool(user_id) and is_developer(user_id)
    user_is_creator   = bool(user_id) and is_creator(user_id)

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

        # FASE 2 — memoria episódica híbrida (FTS + vectorial) ───────────────
        # Se concatena a cross_memory para que el presupuesto adaptativo la
        # mida y recorte junto con el resto de memoria cruzada si no cabe.
        deep_memory  = format_deep_memory(search_user_memory(user_id, message, conv_id))
        cross_memory = cross_memory + deep_memory

        history.append({'role': 'user', 'content': message})

        if conv_id:
            db_run(
                "INSERT INTO messages (conversation_id, user_id, role, content) "
                "VALUES (%s, %s, %s, %s)",
                (conv_id, user_id, 'user', message)
            )
            db_run("UPDATE conversations SET updated_at = NOW() WHERE id = %s", (conv_id,))

    # ── Fuentes de la respuesta anterior ─────────────────────────────────────
    is_pure_source_question     = _is_pure_source_question(message)
    is_combined_source_question = _is_source_question(message) and not is_pure_source_question

    if is_pure_source_question and not temporary and conv_id:
        chunks, web_context = [], ''
        knowledge_context = ''
        previous_sources = _get_last_assistant_sources(conv_id)
        sources_question_block = _format_sources_question_block(previous_sources)
    else:
        if user_is_creator and user_id:
            chunks, web_context = search_knowledge_creator(message, user_id)
            knowledge_context   = format_knowledge_context_creator(
                chunks, web_context, include_dna=True
            )
        else:
            chunks, web_context = search_knowledge(message)
            knowledge_context   = format_knowledge_context(chunks, web_context)
        sources_question_block = (
            _format_combined_sources_reminder() if is_combined_source_question else ''
        )

    # ── Astrología ────────────────────────────────────────────────────────────
    astrology_context = ''
    new_chart_suffix  = ''
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
            target_date  = extract_date_from_message(message)
            person_label = extract_person_from_message(user_id, message)
            astrology_context = format_astrology_context(user_id, target_date, person_label)

    # ── Presupuesto adaptativo (FASE 0) ──────────────────────────────────────
    bloques = _ajustar_a_presupuesto({
        'cross_memory': cross_memory,
        'knowledge':    knowledge_context,
        'astrology':    astrology_context,
    })
    cross_memory      = bloques['cross_memory']
    knowledge_context = bloques['knowledge']
    astrology_context = bloques['astrology']

    # ── Perfil + síntesis viva (FASE 1) ──────────────────────────────────────
    profile_context   = ''
    synthesis_context = ''
    onboarding_suffix = ''
    new_data_suffix   = ''
    if user_id and not temporary:
        profile_context   = format_profile_context(user_id)
        synthesis_context = format_synthesis_context(user_id)

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

    extra_prefix = (developer_prefix + profile_context + synthesis_context + onboarding_suffix +
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

    # ── Perfil: aplicar marcas [GUARDAR_PERFIL: ...] ─────────────────────────
    if user_id and not temporary:
        gaia_text = extract_and_apply_save_marks(user_id, gaia_text)

    # ── Guardar respuesta con fuentes reales ─────────────────────────────────
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
