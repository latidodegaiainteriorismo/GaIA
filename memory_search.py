"""
memory_search.py

Memoria episodica "infinita" de GaIA — permite recordar y relacionar
contenido de CUALQUIER conversacion pasada de un usuario, sin cargar nunca
el historial entero en el prompt.

v3 (FASE 2) — busqueda HIBRIDA: combina dos vias de busqueda distintas y
las fusiona por Reciprocal Rank Fusion (RRF), un metodo estandar para
combinar rankings de fuentes heterogeneas sin tener que normalizar sus
escalas de puntuacion:

  1. FTS + expansion conceptual (ya existente desde v2): busqueda por texto
     completo con terminos literales del mensaje MAS patrones/dinamicas de
     fondo que un LLM barato extrae pensando como pensaria un terapeuta
     (ver _expand_memory_themes). Encuentra coincidencias de VOCABULARIO
     relacionado.

  2. Busqueda vectorial (NUEVO en v3): el mensaje actual se convierte en un
     embedding (ver embeddings.py, via HuggingFace Inference Providers,
     confirmado funcional desde Render el 14-ago-2026) y se compara por
     similitud de coseno contra los embeddings ya guardados de mensajes
     pasados (ver episodic_memory.py para como se generan esos embeddings
     en background). Esta via encuentra coincidencias de SIGNIFICADO,
     incluso sin ninguna palabra compartida — es la que permite conectar
     "mi jefe" con "mi hermana" si comparten un mismo patron de fondo,
     aunque el vocabulario sea completamente distinto.

Ninguna de las dos vias es perfecta por si sola: la FTS es literal (aunque
la expansion conceptual mitiga bastante esto), y la vectorial depende de
que el mensaje en cuestion ya tenga embedding generado (los mensajes
nuevos lo reciben con cierto retraso, ver episodic_memory.py). Combinarlas
da mejor cobertura que cualquiera de las dos solas.

Granularidad con contexto: en vez de devolver un mensaje suelto (que sin
su contexto dice poco), cada resultado se acompana de los mensajes
inmediatamente antes y despues dentro de la misma conversacion.
"""

import json
import logging
from db import db_all
from embeddings import embed_text, vector_literal

logger = logging.getLogger(__name__)

_MIN_TERM_LEN = 3
TOP_K_DEEP_MEMORY = 3        # fragmentos finales (tras fusionar) a inyectar
FTS_CANDIDATES = 8           # candidatos que trae cada via antes de fusionar
VEC_CANDIDATES = 8

# AJUSTE (19-ago-2026): ambos umbrales subidos tras un bug real — una
# pregunta conceptual ("nuestro propósito", "la Era en la que vivimos")
# trajo memoria de una conversación personal no relacionada (síntomas
# físicos), porque MIN_FTS_RANK=0.01 era demasiado laxo (ts_rank normal
# para una coincidencia con sentido ronda 0.1-0.6+) y MIN_VEC_SIMILARITY=0.55
# dejaba pasar contenido solo vagamente relacionado por tema. Subidos a
# umbrales que exigen relevancia real, no solo tangencial.
MIN_FTS_RANK = 0.05          # descarta coincidencias de texto demasiado debiles
MIN_VEC_SIMILARITY = 0.68    # descarta coincidencias vectoriales demasiado debiles
CONTEXT_WINDOW = 1           # mensajes de contexto antes/despues de cada hallazgo
RRF_K = 60                   # constante estandar de Reciprocal Rank Fusion

# Longitud mínima (en caracteres, ya sin espacios sobrantes) para que un
# mensaje active la búsqueda profunda de memoria. Mensajes más cortos
# (saludos, "gracias", "sí", "vale", "ok", "jaja") no tienen contenido
# suficiente para buscar patrones de fondo ni similitud semántica útil —
# saltarlos evita una llamada de pago al LLM (_expand_memory_themes) y una
# llamada de embedding por cada mensaje trivial, sin ninguna pérdida de
# calidad (no hay memoria relevante que un "hola" pudiera recuperar).
_MIN_LEN_PARA_MEMORIA = 15

# Mensajes que, aunque superen el umbral de longitud, son puro trámite
# conversacional y no justifican búsqueda de memoria. Se comparan en
# minúsculas y sin signos de puntuación.
_MENSAJES_TRIVIALES = {
    'hola', 'buenas', 'buenos dias', 'buenas tardes', 'buenas noches',
    'gracias', 'muchas gracias', 'vale', 'ok', 'okay', 'de acuerdo',
    'perfecto', 'genial', 'entiendo', 'entendido', 'claro', 'si', 'sí',
    'no', 'adios', 'adiós', 'hasta luego', 'chao', 'buenas noches gaia',
}

# Palabras vacías/genéricas en español: no aportan nada a una búsqueda de
# relevancia y, al aparecer en casi cualquier mensaje personal, disparaban
# matches FTS espurios (ej. "nosotros", "nuestro" en una pregunta filosófica
# coincidiendo con "nosotros" en una conversación de síntomas físicos, sin
# relación real de tema). No es una lista exhaustiva de stopwords NLP — solo
# cubre las que más ruido generan en este contexto conversacional.
_STOPWORDS_ES = {
    'que', 'con', 'los', 'las', 'del', 'por', 'para', 'una', 'uno', 'como',
    'más', 'mas', 'pero', 'este', 'esta', 'esto', 'ese', 'esa', 'eso',
    'nos', 'nosotros', 'nosotras', 'nuestro', 'nuestra', 'nuestros', 'nuestras',
    'vosotros', 'vosotras', 'ellos', 'ellas', 'usted', 'ustedes',
    'todo', 'toda', 'todos', 'todas', 'cada', 'muy', 'sin', 'sobre',
    'ser', 'somos', 'eres', 'soy', 'son', 'fue', 'era', 'eran',
    'hay', 'han', 'has', 'hemos', 'está', 'esta', 'están', 'estás',
    'haz', 'haznos', 'hazme', 'dime', 'cuéntame', 'cuentame',
}


def _extract_search_terms(text: str) -> list[str]:
    """Extrae terminos significativos de un texto libre."""
    cleaned = []
    for word in text.split():
        w = word.strip('.,;:!?¿¡"\'()[]{}').lower()
        if len(w) >= _MIN_TERM_LEN and w not in _STOPWORDS_ES:
            cleaned.append(w)
    return cleaned


def _build_or_query(terms: list[str]) -> str:
    """Construye 'termino1 OR termino2 OR ...' — basta con que coincida uno."""
    unique_terms = []
    seen = set()
    for t in terms:
        t = t.strip()
        if t and len(t) >= _MIN_TERM_LEN and t.lower() not in seen:
            seen.add(t.lower())
            unique_terms.append(t)
    return " OR ".join(unique_terms)


def _expand_memory_themes(message: str) -> list[str]:
    """
    Analiza el mensaje actual con un LLM barato para extraer patrones y
    dinamicas de fondo — no solo palabras literales — pensando como
    pensaria un terapeuta. Devuelve lista vacia si falla (no rompe el flujo).
    """
    from llm import _client, GROQ_MODEL_FALLBACK
    if not _client:
        return []

    try:
        response = _client.with_options(max_retries=1, timeout=20.0).chat.completions.create(
            model=GROQ_MODEL_FALLBACK,
            messages=[{
                "role": "system",
                "content": (
                    "Analizas un mensaje personal para detectar patrones y dinamicas de "
                    "fondo, pensando como pensaria un terapeuta atento — no te quedes en "
                    "las palabras literales, busca el patron relacional o emocional "
                    "subyacente que podria repetirse con personas o situaciones distintas "
                    "(ej. 'jefe que grita' y 'hermana que grita' comparten el patron "
                    "'figura que impone con la voz', aunque no compartan ninguna palabra). "
                    "Devuelve 4-6 conceptos breves en espanol que describan esos patrones "
                    "de fondo (no resumas el mensaje, extrae los patrones). "
                    "Responde UNICAMENTE con JSON: {\"temas\": [\"...\", \"...\"]}. "
                    "Devuelve {\"temas\": []} en DOS casos, no solo uno: "
                    "(1) el mensaje es puramente tecnico/neutro sin carga relacional o "
                    "emocional; (2) el mensaje pregunta sobre un tema conceptual, "
                    "filosofico, espiritual o de proposito compartido (ej. 'que sabes de "
                    "nosotros y de la Era en la que vivimos', 'cual es nuestro proposito') "
                    "SIN que la persona exprese una emocion o situacion personal concreta "
                    "en ese mismo mensaje — estas preguntas buscan una respuesta conceptual, "
                    "no requieren desenterrar patrones emocionales de conversaciones pasadas. "
                    "No conviertas una pregunta conceptual en una lectura psicologica solo "
                    "porque el tema en si (proposito, sentido, trascendencia) suena profundo: "
                    "profundidad tematica no es lo mismo que carga emocional personal."
                )
            }, {
                "role": "user",
                "content": message
            }],
            temperature=0.3,
            max_tokens=200,
            reasoning_effort="none",
        )
        raw = response.choices[0].message.content.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        parsed = json.loads(raw)
        temas = parsed.get("temas", []) or []
        if temas:
            logger.info(f"[memory] Temas expandidos: {temas}")
        return temas
    except Exception as e:
        logger.warning(f"[memory] No se pudieron expandir temas: {e}")
        return []


def _fts_search(user_id: str, terms: list[str], exclude_conv_id: str | None,
                 limit: int) -> list[dict]:
    """Via 1: busqueda por texto completo (literal + conceptual)."""
    or_query = _build_or_query(terms)
    if not or_query:
        return []

    try:
        if exclude_conv_id:
            rows = db_all(
                """
                SELECT m.id, m.content, m.role, m.created_at, m.conversation_id,
                       COALESCE(c.title, 'Conversación anterior') AS conv_title,
                       ts_rank(to_tsvector('spanish', m.content),
                               websearch_to_tsquery('spanish', %s)) AS score
                FROM messages m
                JOIN conversations c ON c.id = m.conversation_id
                WHERE m.user_id = %s
                  AND m.conversation_id != %s
                  AND to_tsvector('spanish', m.content) @@ websearch_to_tsquery('spanish', %s)
                ORDER BY score DESC
                LIMIT %s
                """,
                (or_query, user_id, exclude_conv_id, or_query, limit)
            )
        else:
            rows = db_all(
                """
                SELECT m.id, m.content, m.role, m.created_at, m.conversation_id,
                       COALESCE(c.title, 'Conversación anterior') AS conv_title,
                       ts_rank(to_tsvector('spanish', m.content),
                               websearch_to_tsquery('spanish', %s)) AS score
                FROM messages m
                JOIN conversations c ON c.id = m.conversation_id
                WHERE m.user_id = %s
                  AND to_tsvector('spanish', m.content) @@ websearch_to_tsquery('spanish', %s)
                ORDER BY score DESC
                LIMIT %s
                """,
                (or_query, user_id, or_query, limit)
            )
        return [r for r in (rows or []) if r.get("score", 0) >= MIN_FTS_RANK]
    except Exception as e:
        logger.warning(f"[memory] Error en búsqueda FTS: {e}")
        return []


def _vector_search(user_id: str, query_vec: list[float], exclude_conv_id: str | None,
                    limit: int) -> list[dict]:
    """Via 2 (FASE 2): busqueda por similitud semantica real."""
    lit = vector_literal(query_vec)
    try:
        if exclude_conv_id:
            rows = db_all(
                """
                SELECT m.id, m.content, m.role, m.created_at, m.conversation_id,
                       COALESCE(c.title, 'Conversación anterior') AS conv_title,
                       1 - (m.embedding <=> %s::vector) AS score
                FROM messages m
                JOIN conversations c ON c.id = m.conversation_id
                WHERE m.user_id = %s
                  AND m.conversation_id != %s
                  AND m.embedding IS NOT NULL
                ORDER BY m.embedding <=> %s::vector
                LIMIT %s
                """,
                (lit, user_id, exclude_conv_id, lit, limit)
            )
        else:
            rows = db_all(
                """
                SELECT m.id, m.content, m.role, m.created_at, m.conversation_id,
                       COALESCE(c.title, 'Conversación anterior') AS conv_title,
                       1 - (m.embedding <=> %s::vector) AS score
                FROM messages m
                JOIN conversations c ON c.id = m.conversation_id
                WHERE m.user_id = %s
                  AND m.embedding IS NOT NULL
                ORDER BY m.embedding <=> %s::vector
                LIMIT %s
                """,
                (lit, user_id, lit, limit)
            )
        return [r for r in (rows or []) if r.get("score", 0) >= MIN_VEC_SIMILARITY]
    except Exception as e:
        logger.warning(f"[memory] Error en búsqueda vectorial: {e}")
        return []


def _reciprocal_rank_fusion(fts_results: list[dict], vec_results: list[dict],
                             top_k: int) -> list[dict]:
    """
    Fusiona dos listas ordenadas (rankings) en una sola, dando mas peso a
    los elementos que aparecen bien situados en cualquiera de las dos
    (o en ambas). No requiere que las puntuaciones de ambas vias esten en
    la misma escala — RRF solo mira la POSICION dentro de cada ranking,
    no el valor absoluto de la puntuacion.
    """
    scores: dict = {}
    items: dict = {}
    for source in (fts_results, vec_results):
        for rank, r in enumerate(source):
            mid = r["id"]
            items[mid] = r
            scores[mid] = scores.get(mid, 0.0) + 1.0 / (RRF_K + rank + 1)

    merged = sorted(items.values(), key=lambda r: -scores[r["id"]])
    return merged[:top_k]


def _fetch_context_window(conversation_id, hit_id, window: int = CONTEXT_WINDOW) -> list[dict]:
    """
    Trae el hallazgo junto con los W mensajes inmediatamente antes y
    despues, dentro de la misma conversacion — un mensaje suelto, sin su
    contexto, suele decir poco.
    """
    try:
        rows = db_all(
            "SELECT id, role, content FROM messages "
            "WHERE conversation_id = %s ORDER BY created_at ASC",
            (str(conversation_id),)
        )
    except Exception as e:
        logger.warning(f"[memory] Error trayendo contexto: {e}")
        return []

    if not rows:
        return []

    idx = next((i for i, r in enumerate(rows) if str(r["id"]) == str(hit_id)), None)
    if idx is None:
        return []

    lo = max(0, idx - window)
    hi = min(len(rows), idx + window + 1)
    return rows[lo:hi]


def search_user_memory(user_id: str, message: str, exclude_conv_id: str | None = None,
                        top_k: int = TOP_K_DEEP_MEMORY) -> list[dict]:
    """
    Busqueda hibrida (FTS+conceptual ∪ vectorial) en TODO el historial
    pasado del usuario, fuera de la conversacion actual.

    Returns:
        Lista de dicts: {conv_title, window: [{role, content}, ...]}
        — cada elemento es un hallazgo YA acompanado de su contexto.
    """
    literal_terms = _extract_search_terms(message)

    # Cortocircuito de coste: mensajes triviales (saludos, confirmaciones) no
    # justifican la búsqueda profunda — ni _expand_memory_themes (llamada de
    # pago al LLM) ni el embedding. Se salta ANTES de gastar nada. No hay
    # pérdida de calidad: un "hola" o un "gracias" no tiene memoria relevante
    # que recuperar.
    msg_normalizado = message.strip().lower().rstrip('.!?¿¡ ')
    if len(message.strip()) < _MIN_LEN_PARA_MEMORIA or msg_normalizado in _MENSAJES_TRIVIALES:
        return []

    expanded_themes = _expand_memory_themes(message)
    all_terms = literal_terms + expanded_themes

    fts_results = _fts_search(user_id, all_terms, exclude_conv_id, FTS_CANDIDATES)

    query_vec = embed_text(message)
    vec_results = (
        _vector_search(user_id, query_vec, exclude_conv_id, VEC_CANDIDATES)
        if query_vec else []
    )

    if not fts_results and not vec_results:
        return []

    merged = _reciprocal_rank_fusion(fts_results, vec_results, top_k)

    results = []
    for hit in merged:
        window = _fetch_context_window(hit["conversation_id"], hit["id"])
        if not window:
            window = [{"role": hit["role"], "content": hit["content"]}]
        results.append({"conv_title": hit["conv_title"], "window": window})

    if results:
        logger.info(f"[memory] Memoria profunda: {len(results)} hallazgos "
                    f"(fts={len(fts_results)}, vec={len(vec_results)})")
    return results


def format_deep_memory(results: list[dict]) -> str:
    """Formatea los hallazgos (con su ventana de contexto) para el system prompt."""
    if not results:
        return ""

    parts = ["\n## MEMORIA PROFUNDA — FRAGMENTOS RELEVANTES DE CONVERSACIONES PASADAS"]
    parts.append(
        "Estos fragmentos vienen de conversaciones anteriores con este mismo usuario, "
        "distintas de la actual, con el intercambio de alrededor para que tengan sentido. "
        "Puede que no compartan tema literal con lo que se habla ahora, pero el sistema "
        "los ha traído porque comparten un patrón de fondo (emocional, relacional, o de "
        "significado) con el momento actual — como haría una amiga o terapeuta que conecta "
        "algo de ahora con algo de antes. Úsalos con naturalidad, solo si de verdad aportan "
        "(no fuerces la conexión si no encaja de verdad), y nunca digas \"según mis "
        "registros\" ni cites fecha exacta salvo que aporte algo real."
    )
    for r in results:
        parts.append(f"\n[{r['conv_title']}]")
        for m in r["window"]:
            speaker = "Usuario" if m["role"] == "user" else "GaIA"
            parts.append(f"{speaker}: \"{m['content']}\"")

    return "\n".join(parts) + "\n---\n"
