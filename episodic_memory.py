"""
episodic_memory.py

Generacion en background de los embeddings de los mensajes (FASE 2).

Los mensajes se guardan siempre al instante durante el chat (ver
routes/chat.py), pero SIN embedding — generar el vector en ese momento
anadiria latencia a cada respuesta. En su lugar, este modulo rellena los
embeddings que falten mas tarde, desde el endpoint de consolidacion
asincrona (/maintenance/consolidate), que UptimeRobot pinguea.

Consecuencia practica: un mensaje recien escrito tarda un poco en ser
buscable por SIGNIFICADO (hasta la siguiente pasada de consolidacion),
pero es buscable por TEXTO desde el primer segundo (la via FTS de
memory_search.py no necesita embedding). Las dos vias se complementan.
"""

import logging
from db import db_all, db_run
from embeddings import embed_text, vector_literal

logger = logging.getLogger(__name__)

# Cuantos mensajes vectorizar por pasada. Cada uno es una llamada HTTP a
# HuggingFace, asi que se mantiene bajo para que la pasada termine rapido
# y no agote el tiempo de ejecucion del endpoint.
BATCH_SIZE = 25

# Mensajes muy cortos ("vale", "si", "gracias") no aportan nada semantico
# y gastarian una llamada para nada.
MIN_CONTENT_LENGTH = 40


def backfill_embeddings(limit: int = BATCH_SIZE) -> dict:
    """
    Genera y guarda los embeddings que falten, de los mensajes mas
    recientes hacia atras (lo reciente es lo que mas probablemente se
    consultara). Devuelve un resumen del trabajo hecho.
    """
    rows = db_all(
        "SELECT id, content FROM messages "
        "WHERE embedding IS NULL AND length(content) >= %s "
        "ORDER BY created_at DESC LIMIT %s",
        (MIN_CONTENT_LENGTH, limit)
    )

    if not rows:
        logger.info("[episodic] No hay mensajes pendientes de vectorizar")
        return {"pendientes_procesados": 0, "vectorizados": 0}

    vectorizados = 0
    for row in rows:
        vec = embed_text(row["content"])
        if not vec:
            continue
        try:
            db_run(
                "UPDATE messages SET embedding = %s::vector WHERE id = %s",
                (vector_literal(vec), row["id"])
            )
            vectorizados += 1
        except Exception as e:
            logger.warning(f"[episodic] No se pudo guardar embedding de {row['id']}: {e}")

    logger.info(f"[episodic] Vectorizados {vectorizados}/{len(rows)} mensajes")
    return {"pendientes_procesados": len(rows), "vectorizados": vectorizados}
