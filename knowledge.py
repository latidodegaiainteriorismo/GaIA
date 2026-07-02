import unicodedata
from db import db_all

# Carpetas de autoría propia — su contenido tiene prioridad sobre cualquier otro
# chunk recuperado, y sobre el conocimiento general de GaIA, en caso de conflicto.
PRIORITY_FOLDERS = {"Mónica Martos", "Adrián Lozano", "Enciclopedia de la Biología"}

# Umbral mínimo de ts_rank para considerar un resultado de FTS como relevante
# (y no ruido). Ajustar viendo los valores reales en los logs de [knowledge].
MIN_FTS_RANK = 0.02


def _normalize(s: str) -> str:
    """Quita acentos y pasa a minúsculas, para comparar categorías sin depender
    de que el encoding del nombre de carpeta sea exacto (ver bug de encoding
    corregido en knowledge/ — esto es una defensa extra a futuro)."""
    s = unicodedata.normalize('NFKD', s or '').encode('ascii', 'ignore').decode('ascii')
    return s.strip().lower()


_PRIORITY_NORM = {_normalize(f) for f in PRIORITY_FOLDERS}


def _is_priority(category: str) -> bool:
    return _normalize(category) in _PRIORITY_NORM


def search_knowledge(query: str, top_k: int = 3) -> list[dict]:
    """
    Recupera chunks relevantes de Knowledge usando PostgreSQL FTS en español,
    con fallback a ILIKE si el FTS no encuentra nada.

    - Filtra resultados de FTS por debajo de MIN_FTS_RANK (ruido, no relevancia real).
    - Prioriza los chunks de las carpetas de autoría propia (PRIORITY_FOLDERS)
      sobre el resto, manteniendo el orden por relevancia dentro de cada grupo.
    - Devuelve [] si no hay nada realmente relevante — es la señal que usa
      llm.py para decidir si GaIA responde con contexto o con su saber general.
    """
    try:
        rows = db_all(
            """
            SELECT content, source, category,
                   ts_rank(
                       to_tsvector('spanish', content),
                       plainto_tsquery('spanish', %s)
                   ) AS rank
            FROM knowledge_chunks
            WHERE to_tsvector('spanish', content) @@ plainto_tsquery('spanish', %s)
            ORDER BY rank DESC
            LIMIT %s
            """,
            (query, query, top_k * 3)  # pedimos de más para poder filtrar por umbral
        )

        rows = [r for r in (rows or []) if r.get('rank', 0) >= MIN_FTS_RANK]

        if rows:
            print(f"[knowledge] FTS: {len(rows)} chunks por encima del umbral ({MIN_FTS_RANK})")
        else:
            # Fallback: ILIKE con la palabra más distintiva del query
            words = [w for w in query.split() if len(w) > 4]
            if words:
                keyword = words[0]
                rows = db_all(
                    """
                    SELECT content, source, category, 0.01 AS rank
                    FROM knowledge_chunks
                    WHERE content ILIKE %s
                    LIMIT %s
                    """,
                    (f"%{keyword}%", top_k * 3)
                ) or []
                if rows:
                    print(f"[knowledge] ILIKE fallback: {len(rows)} chunks")

        if not rows:
            print("[knowledge] Sin resultados relevantes — GaIA responderá desde su saber general")
            return []

        # Prioridad: carpetas de autoría propia primero, luego por relevancia
        rows.sort(key=lambda r: (not _is_priority(r.get('category', '')), -r.get('rank', 0)))

        return rows[:top_k]

    except Exception as e:
        print(f"[knowledge] Error: {e}")
        return []


def format_knowledge_context(chunks: list[dict]) -> str:
    """Formatea los chunks recuperados, marcando cuáles vienen de una fuente
    prioritaria para que el modelo les dé más peso en caso de conflicto."""
    if not chunks:
        return ""
    lines = []
    for chunk in chunks:
        source   = chunk.get("source", "").split("/")[-1].replace(".txt", "")
        category = chunk.get("category", "")
        tag      = f"{source} — {category}" if category else source
        if _is_priority(category):
            tag += " ★ fuente prioritaria"
        lines.append(f"— {tag}")
        lines.append(chunk.get("content", "").strip())
        lines.append("")
    return "\n".join(lines).strip()
