"""
knowledge.py

RAG de GaIA — v6.1 (Opción B: query understanding + routing por documento)

Flujo:
  1. SIEMPRE se busca en Mónica Martos y Enciclopedia de la Biología
     (incondicional, forzado por código, no depende del LLM).
  2. Se enruta la pregunta contra el catálogo de documentos (query_router.py)
     y se buscan los documentos que Groq considera relevantes.
  3. Si el total de chunks es insuficiente, o el router marca needs_web,
     se recurre a búsqueda web como último recurso (websearch.py).

NOTA (jul-2026 v6.1): la búsqueda de texto completo usaba plainto_tsquery,
que exige que TODAS las palabras de la consulta aparezcan en el mismo chunk
(AND implícito). Sobre fragmentos cortos (~300-800 caracteres) esto fallaba
sistemáticamente incluso cuando el documento SÍ trataba el tema — bastaba con
que una sola palabra clave no apareciera literalmente en ese chunk concreto
para que la búsqueda devolviera cero resultados. Se cambia a una consulta
tipo "encuentra CUALQUIERA de estos términos" (OR), dejando que el ranking
(ts_rank) priorice los fragmentos que coincidan en más términos — mucho más
tolerante y realista para chunks cortos.
"""

import logging
from db import db_all
from query_router import preprocess_query
from websearch import search_web

logger = logging.getLogger(__name__)

MIN_CHUNKS_ANTES_DE_WEB = 2   # si hay menos que esto, probamos la web también
TOP_K_MONICA = 3              # chunks garantizados de Mónica / Enciclopedia
TOP_K_POR_DOCUMENTO = 2       # chunks por cada documento adicional enrutado
TOP_K_TOTAL = 6               # tope de chunks totales inyectados en el contexto

# Palabras demasiado cortas/genéricas no aportan nada a una búsqueda OR y
# solo añaden ruido (o, en algunos casos, provocan errores de sintaxis en
# tsquery si quedan sueltas). Se descartan antes de construir la consulta.
_MIN_TERM_LEN = 3


def _extract_search_terms(text: str) -> list[str]:
    """
    Extrae términos de búsqueda significativos de un texto libre (una
    pregunta completa, no una lista ya curada de keywords). Filtra palabras
    muy cortas (artículos, preposiciones) que no aportan valor a una
    búsqueda por texto completo.
    """
    cleaned = []
    for word in text.split():
        w = word.strip('.,;:!?¿¡"\'()[]{}').lower()
        if len(w) >= _MIN_TERM_LEN:
            cleaned.append(w)
    return cleaned


def _build_or_query(terms: list[str]) -> str:
    """
    Construye una cadena de consulta tipo "término1 OR término2 OR ..." para
    websearch_to_tsquery — encuentra chunks que contengan CUALQUIERA de los
    términos, no todos a la vez. ts_rank se encarga de puntuar más alto los
    chunks que coincidan en más términos, así que no perdemos precisión,
    solo dejamos de exigir una coincidencia perfecta de todas las palabras.
    """
    unique_terms = []
    seen = set()
    for t in terms:
        t = t.strip()
        if t and len(t) >= _MIN_TERM_LEN and t.lower() not in seen:
            seen.add(t.lower())
            unique_terms.append(t)
    return " OR ".join(unique_terms)


def _fts_search(terms: list[str], source_filter: str = None, limit: int = 3) -> list[dict]:
    """
    Búsqueda FTS en español usando OR entre términos (ver _build_or_query),
    opcionalmente restringida a un source ILIKE.
    """
    if limit <= 0:
        return []

    or_query = _build_or_query(terms)
    if not or_query:
        return []

    try:
        if source_filter:
            rows = db_all(
                """
                SELECT content, source, category,
                       ts_rank(to_tsvector('spanish', content),
                               websearch_to_tsquery('spanish', %s)) AS rank
                FROM knowledge_chunks
                WHERE source ILIKE %s
                  AND to_tsvector('spanish', content) @@ websearch_to_tsquery('spanish', %s)
                ORDER BY rank DESC
                LIMIT %s
                """,
                (or_query, f"%{source_filter}%", or_query, limit)
            )
        else:
            rows = db_all(
                """
                SELECT content, source, category,
                       ts_rank(to_tsvector('spanish', content),
                               websearch_to_tsquery('spanish', %s)) AS rank
                FROM knowledge_chunks
                WHERE to_tsvector('spanish', content) @@ websearch_to_tsquery('spanish', %s)
                ORDER BY rank DESC
                LIMIT %s
                """,
                (or_query, or_query, limit)
            )
        return rows or []
    except Exception as e:
        logger.warning(f"[knowledge] FTS error (source={source_filter}): {e}")
        return []


def _ilike_fallback(keyword: str, source_filter: str = None, limit: int = 2) -> list[dict]:
    """Fallback simple si el FTS no encuentra nada."""
    if limit <= 0:
        return []
    try:
        if source_filter:
            rows = db_all(
                """
                SELECT content, source, category, 0.1 AS rank
                FROM knowledge_chunks
                WHERE source ILIKE %s AND content ILIKE %s
                LIMIT %s
                """,
                (f"%{source_filter}%", f"%{keyword}%", limit)
            )
        else:
            rows = db_all(
                """
                SELECT content, source, category, 0.1 AS rank
                FROM knowledge_chunks
                WHERE content ILIKE %s
                LIMIT %s
                """,
                (f"%{keyword}%", limit)
            )
        return rows or []
    except Exception as e:
        logger.warning(f"[knowledge] ILIKE error: {e}")
        return []


def _search_monica(message: str) -> list[dict]:
    """
    Paso 1 — SIEMPRE, sin excepción.
    Busca en Mónica Martos y Enciclopedia de la Biología.
    """
    terms = _extract_search_terms(message)
    chunks = _fts_search(terms, source_filter="Mónica", limit=TOP_K_MONICA)
    restante = TOP_K_MONICA - len(chunks)
    chunks += _fts_search(terms, source_filter="Enciclopedia de la Biología",
                           limit=restante)

    if not chunks:
        words = [w for w in message.split() if len(w) > 4]
        keyword = words[0] if words else message
        chunks = _ilike_fallback(keyword, source_filter="Mónica", limit=TOP_K_MONICA)

    if chunks:
        logger.info(f"[knowledge] Mónica/Enciclopedia: {len(chunks)} chunks")
    else:
        logger.info("[knowledge] Mónica/Enciclopedia: sin resultados")
    return chunks


def _search_routed_documents(message: str, documents: list, keywords: list) -> list[dict]:
    """
    Paso 2 — busca en los documentos que Groq consideró relevantes.
    """
    if not documents:
        return []

    terms = keywords if keywords else _extract_search_terms(message)
    chunks = []

    for doc_key in documents:
        found = _fts_search(terms, source_filter=doc_key, limit=TOP_K_POR_DOCUMENTO)
        if not found:
            palabra = terms[0] if terms else doc_key
            found = _ilike_fallback(palabra, source_filter=doc_key, limit=TOP_K_POR_DOCUMENTO)
        chunks.extend(found)

    if chunks:
        logger.info(f"[knowledge] Documentos enrutados {documents}: {len(chunks)} chunks")
    return chunks


def search_knowledge(message: str, top_k: int = TOP_K_TOTAL):
    """
    Orquesta los 3 pasos del RAG v6.1.
    Devuelve (chunks, web_context) — web_context ya viene formateado si se usó.
    """
    # Paso 1 — Mónica Martos, incondicional
    chunks = _search_monica(message)

    # Paso 2 — Query understanding + routing por documento
    route_info = preprocess_query(message)
    routed_chunks = _search_routed_documents(
        message,
        route_info.get("documents", []),
        route_info.get("keywords", [])
    )
    chunks.extend(routed_chunks)

    # Recortar al total máximo, priorizando Mónica (ya está primero en la lista)
    chunks = chunks[:top_k]

    # Paso 3 — Fallback web si hace falta
    web_context = ""
    necesita_web = route_info.get("needs_web", False) or len(chunks) < MIN_CHUNKS_ANTES_DE_WEB
    if necesita_web:
        query_web = " ".join(route_info.get("keywords", [])) or message
        web_context = search_web(query_web)
        if web_context:
            logger.info("[knowledge] Fallback web activado")

    return chunks, web_context


def format_knowledge_context(chunks: list, web_context: str = "") -> str:
    """Formatea los chunks documentales + el contexto web (si lo hay)."""
    parts = []

    if chunks:
        lines = ["[Conocimiento relevante de la biblioteca de GaIA]"]
        for chunk in chunks:
            source = chunk.get("source", "").split("/")[-1].replace(".txt", "")
            lines.append(f"\n— {source}")
            lines.append(chunk.get("content", "").strip())
        parts.append("\n".join(lines))

    if web_context:
        parts.append(web_context)

    return "\n\n".join(parts)
