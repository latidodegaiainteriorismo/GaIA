from db import db_all


def search_knowledge(query: str, top_k: int = 3) -> list[dict]:
    """
    Recupera chunks relevantes usando PostgreSQL FTS en español.
    Fallback a ILIKE si FTS no devuelve resultados.
    """
    try:
        # FTS con stemming en español
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
            (query, query, top_k)
        )

        if rows:
            print(f"[knowledge] FTS: {len(rows)} chunks encontrados")
            return rows

        # Fallback: ILIKE con la palabra más distintiva del query
        words = [w for w in query.split() if len(w) > 4]
        if not words:
            return []
        keyword = words[0]
        rows = db_all(
            """
            SELECT content, source, category, 0.1 AS rank
            FROM knowledge_chunks
            WHERE content ILIKE %s
            LIMIT %s
            """,
            (f"%{keyword}%", top_k)
        )
        if rows:
            print(f"[knowledge] ILIKE fallback: {len(rows)} chunks")
        return rows or []

    except Exception as e:
        print(f"[knowledge] Error: {e}")
        return []


def format_knowledge_context(chunks: list[dict]) -> str:
    if not chunks:
        return ""
    lines = ["[Conocimiento relevante de la biblioteca de GaIA]"]
    for chunk in chunks:
        source = chunk.get("source", "").split("/")[-1].replace(".txt", "")
        lines.append(f"\n— {source}")
        lines.append(chunk.get("content", "").strip())
    return "\n".join(lines)
