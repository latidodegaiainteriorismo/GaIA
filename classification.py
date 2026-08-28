"""
classification.py

Clasificación automática de las conversaciones del creator (Mónica y
Adrián) en tres categorías de conocimiento distintas de la memoria
personal normal:

  1. ESTRUCTURA BASAL — ajustes sobre el propio funcionamiento de GaIA:
     cómo debe responder, analizar, priorizar. Se aplica GLOBALMENTE, a
     las conversaciones de cualquier usuario — no es memoria de nadie en
     particular, es el comportamiento de GaIA en sí. Cada nota tiene un
     interruptor 'active': revertir un aprendizaje es tan simple como
     desactivarlo, sin tocar el ADN escrito a mano.

  2. ENCICLOPEDIA DE LA BIOLOGÍA — contenido que amplía o matiza el
     corpus de la Escuela de la Biología, dicho en conversación viva en
     vez de escrito directamente. Se guarda en knowledge_chunks, privado
     (visibility='creator') hasta que se promueva a 'all'.

  3. ASTROLOGÍA — aprendizajes sobre interpretación de cartas natales,
     tránsitos u otros aspectos astrológicos. Mismo patrón que el punto 2.

Se ejecuta como parte del mismo ciclo de mantenimiento que ya usa la
síntesis viva (routes/maintenance.py, pingueado por UptimeRobot cada
pocos minutos) — sin infraestructura nueva, con el mismo ritmo "lo más
inmediato posible dentro de lo razonable" que ya rige el resto del
sistema de memoria.
"""

import json
import logging
from db import db_all, db_one, db_run
from config import CREATOR_EMAIL, DEVELOPER_EMAIL
from embeddings import embed_text, vector_literal

logger = logging.getLogger(__name__)

# Nº mínimo de mensajes nuevos (desde la última pasada) para disparar una
# clasificación de una conversación. El usuario pidió "tras cada mensaje o
# tras 5 o 10" — 5 es el punto medio: suficientemente inmediato sin gastar
# una llamada de clasificación en cada turno individual.
_MIN_MSGS_PARA_CLASIFICAR = 5

# Tope de mensajes por pasada, para no mandar transcripciones gigantes si
# una conversación lleva mucho tiempo sin clasificarse (p.ej. tras activar
# esta función por primera vez sobre un historial ya largo).
_MAX_MSGS_POR_PASADA = 30

_SOURCE_CONVERSACION_VIVA = 'Conversación viva'

_CATEGORY_MAP = {
    'enciclopedia': 'Enciclopedia de la Biología',
    'astrologia':   'Astrología',
}


def _get_creator_user_ids() -> list[str]:
    """IDs de usuario de Mónica y Adrián — únicas fuentes de clasificación."""
    rows = db_all(
        "SELECT id FROM users WHERE email = ANY(%s)",
        ([CREATOR_EMAIL, DEVELOPER_EMAIL],)
    )
    return [str(r['id']) for r in (rows or [])]


def _get_conversations_with_new_messages(user_id: str) -> list[dict]:
    """
    Conversaciones del usuario con al menos _MIN_MSGS_PARA_CLASIFICAR
    mensajes nuevos desde la última clasificación (o desde el principio,
    si nunca se ha clasificado esa conversación).
    """
    rows = db_all(
        """
        SELECT c.id AS conversation_id,
               ccs.last_classified_message_id,
               COUNT(m.id) FILTER (
                   WHERE ccs.last_classified_message_id IS NULL
                      OR m.created_at > (
                          SELECT created_at FROM messages WHERE id = ccs.last_classified_message_id
                      )
               ) AS pending_count
        FROM conversations c
        LEFT JOIN conversation_classification_state ccs ON ccs.conversation_id = c.id
        LEFT JOIN messages m ON m.conversation_id = c.id
        WHERE c.user_id = %s
        GROUP BY c.id, ccs.last_classified_message_id
        HAVING COUNT(m.id) FILTER (
            WHERE ccs.last_classified_message_id IS NULL
               OR m.created_at > (
                   SELECT created_at FROM messages WHERE id = ccs.last_classified_message_id
               )
        ) >= %s
        """,
        (user_id, _MIN_MSGS_PARA_CLASIFICAR)
    )
    return rows or []


def _get_pending_messages(conversation_id: str, last_classified_message_id: str | None) -> list[dict]:
    """Mensajes de la conversación pendientes de clasificar, en orden cronológico."""
    if last_classified_message_id:
        rows = db_all(
            """
            SELECT id, role, content, created_at FROM messages
            WHERE conversation_id = %s
              AND created_at > (SELECT created_at FROM messages WHERE id = %s)
            ORDER BY created_at ASC
            LIMIT %s
            """,
            (conversation_id, last_classified_message_id, _MAX_MSGS_POR_PASADA)
        )
    else:
        rows = db_all(
            """
            SELECT id, role, content, created_at FROM messages
            WHERE conversation_id = %s
            ORDER BY created_at ASC
            LIMIT %s
            """,
            (conversation_id, _MAX_MSGS_POR_PASADA)
        )
    return rows or []


def _build_classification_prompt(transcript: str) -> str:
    return f"""Eres el módulo de clasificación de aprendizajes de GaIA.

Analiza este fragmento de conversación entre el creator de GaIA (Mónica o
Adrián) y GaIA misma. Identifica si contiene contenido que pertenezca a
alguna de estas 3 categorías. Un mismo fragmento puede no tener ninguna,
una, o varias.

1. "estructura_basal" — instrucciones o ajustes sobre el propio
   funcionamiento de GaIA: cómo debe responder, qué debe priorizar, cómo
   debe analizar una pregunta, tono, formato, qué evitar. NO es una
   corrección puntual de un dato factual — es un patrón de comportamiento
   que debería aplicarse siempre en adelante.

2. "enciclopedia" — contenido sustantivo que amplía, matiza o desarrolla
   el corpus de la Escuela de la Biología / Enciclopedia de la Biología
   (biodescodificación, los reinos, Feng Shui, Geobiología, la Frecuencia
   Fuente, etc.) — ideas nuevas dichas en conversación, no solo repetición
   de lo ya escrito.

3. "astrologia" — aprendizajes sobre CÓMO interpretar cartas natales,
   tránsitos, aspectos u otros elementos de astrología — un criterio de
   lectura, no una carta natal concreta de una persona.

Para cada hallazgo, reescribe el contenido de forma clara y reutilizable
—como si fuera una instrucción o un párrafo de referencia independiente,
no un fragmento de diálogo—, sin perder el sentido original. Si el
fragmento no aporta nada nuevo a ninguna categoría, no lo incluyas.

Responde ÚNICAMENTE con JSON válido, sin texto adicional, sin markdown:
{{"items": [{{"category": "estructura_basal|enciclopedia|astrologia", "content": "...", "excerpt": "cita breve opcional o null"}}]}}

Si no hay nada relevante, responde {{"items": []}}.

Fragmento de conversación:
{transcript}"""


def _classify_transcript(transcript: str) -> list[dict]:
    """Llama al LLM de clasificación (Gemini) y devuelve la lista de items."""
    from llm import _client
    from config import GEMINI_MODEL
    if not _client:
        return []

    prompt = _build_classification_prompt(transcript)
    try:
        response = _client.with_options(max_retries=1, timeout=25.0).chat.completions.create(
            model=GEMINI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=800,
            temperature=0.3,
            reasoning_effort="low",
        )
        raw = response.choices[0].message.content.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        parsed = json.loads(raw)
        items = parsed.get("items", []) or []
        return [
            it for it in items
            if it.get("category") in ("estructura_basal", "enciclopedia", "astrologia")
            and it.get("content", "").strip()
        ]
    except Exception as e:
        logger.warning(f"[classification] Error clasificando: {e}")
        return []


def _save_item(item: dict, conversation_id: str, user_id: str) -> None:
    """Guarda un item clasificado en su destino correspondiente."""
    category = item["category"]
    content  = item["content"].strip()
    excerpt  = (item.get("excerpt") or None)

    if category == "estructura_basal":
        db_run(
            """
            INSERT INTO basal_notes (content, source_excerpt, conversation_id, created_by, active)
            VALUES (%s, %s, %s, %s, true)
            """,
            (content, excerpt, conversation_id, user_id)
        )
        logger.info(f"[classification] Nueva nota de Estructura Basal: {content[:80]!r}")
        return

    # enciclopedia / astrologia -> knowledge_chunks, privado hasta promoción
    knowledge_category = _CATEGORY_MAP[category]
    vec = embed_text(content)
    embedding_lit = vector_literal(vec) if vec else None
    db_run(
        """
        INSERT INTO knowledge_chunks (content, source, category, embedding, visibility)
        VALUES (%s, %s, %s, %s, 'creator')
        """,
        (content, _SOURCE_CONVERSACION_VIVA, knowledge_category, embedding_lit)
    )
    logger.info(f"[classification] Nuevo conocimiento ({knowledge_category}): {content[:80]!r}")


def _update_classification_state(conversation_id: str, last_message_id: str) -> None:
    db_run(
        """
        INSERT INTO conversation_classification_state (conversation_id, last_classified_message_id, classified_at)
        VALUES (%s, %s, NOW())
        ON CONFLICT (conversation_id)
        DO UPDATE SET last_classified_message_id = EXCLUDED.last_classified_message_id,
                      classified_at = NOW()
        """,
        (conversation_id, last_message_id)
    )


def run_classification_for_creator() -> dict:
    """
    Punto de entrada llamado desde /maintenance/consolidate. Revisa las
    conversaciones de Mónica y Adrián, clasifica las que tengan suficientes
    mensajes nuevos, y guarda los hallazgos en su destino correspondiente.
    """
    creator_ids = _get_creator_user_ids()
    if not creator_ids:
        return {'conversaciones_revisadas': 0, 'items_guardados': 0}

    conversaciones_revisadas = 0
    items_guardados = 0

    for user_id in creator_ids:
        pendientes = _get_conversations_with_new_messages(user_id)
        for conv in pendientes:
            conv_id = str(conv['conversation_id'])
            last_id = str(conv['last_classified_message_id']) if conv['last_classified_message_id'] else None

            messages = _get_pending_messages(conv_id, last_id)
            if not messages:
                continue

            transcript = '\n'.join(
                f"{'Creator' if m['role'] == 'user' else 'GaIA'}: {m['content']}"
                for m in messages
            )

            items = _classify_transcript(transcript)
            for item in items:
                _save_item(item, conv_id, user_id)
                items_guardados += 1

            _update_classification_state(conv_id, str(messages[-1]['id']))
            conversaciones_revisadas += 1

    if conversaciones_revisadas:
        logger.info(f"[classification] {conversaciones_revisadas} conversaciones revisadas, "
                    f"{items_guardados} items guardados")

    return {
        'conversaciones_revisadas': conversaciones_revisadas,
        'items_guardados': items_guardados,
    }
