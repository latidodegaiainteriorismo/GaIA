"""
websearch.py

Fallback web cuando el conocimiento propio de GaIA no basta.
Usa duckduckgo-search (sin API key, sin coste).
Solo se activa cuando query_router.preprocess_query() marca needs_web=True.
"""

import logging

logger = logging.getLogger(__name__)

try:
    from duckduckgo_search import DDGS
    _DDG_AVAILABLE = True
except ImportError:
    _DDG_AVAILABLE = False
    logger.warning("[websearch] duckduckgo-search no instalado — fallback web desactivado")


def search_web(query: str, max_results: int = 3) -> str:
    """
    Busca en DuckDuckGo y devuelve un bloque de texto formateado
    listo para inyectar en el contexto de Groq.
    Devuelve string vacío si falla o no está disponible.
    """
    if not _DDG_AVAILABLE:
        return ""

    try:
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results, region="es-es"):
                results.append(r)

        if not results:
            return ""

        lines = ["[Información complementaria de la web]"]
        for r in results:
            title = r.get("title", "")
            body = r.get("body", "")
            lines.append(f"\n— {title}")
            lines.append(body.strip())

        logger.info(f"[websearch] {len(results)} resultados para: {query}")
        return "\n".join(lines)

    except Exception as e:
        logger.warning(f"[websearch] Error: {e}")
        return ""
