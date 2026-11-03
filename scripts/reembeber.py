"""
Recalcula con Gemini los embeddings ya guardados en messages.embedding.

Por qué: hasta el 3-nov-2026 los vectores se generaban con otro modelo (MiniLM de
HuggingFace). Los vectores de modelos distintos no son comparables, así que los
mensajes antiguos hay que vectorizarlos de nuevo para que la búsqueda por significado
de la memoria funcione. (audio_chunks y knowledge_chunks también tienen vectores, pero
ninguna consulta los usa: la búsqueda en ellos es por texto.)

Los embeddings son un dato DERIVADO del texto: no se pierde nada, siempre se pueden
regenerar. Aun así, --aplicar escribe en producción, así que es opt-in.

Uso (desde la raíz del backend, con DATABASE_URL y GEMINI_API_KEY en el entorno):
    python scripts/reembeber.py              # cuenta cuántos mensajes hay y estima; no escribe
    python scripts/reembeber.py --aplicar    # recalcula y guarda (reanudable)
    python scripts/reembeber.py --aplicar --limite 200   # solo una tanda de prueba

Es reanudable: no se toca el esquema, así que recorre los mensajes del más reciente al más
antiguo y guarda el último created_at procesado en ~/.gaia_backup/reembeber_estado.txt.
"""
import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import embeddings  # noqa: E402

MIN_CONTENT_LENGTH = 40          # mismo criterio que episodic_memory.backfill_embeddings
ESTADO = Path.home() / ".gaia_backup" / "reembeber_estado.txt"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--aplicar", action="store_true", help="escribe los vectores (si no, solo cuenta)")
    ap.add_argument("--limite", type=int, default=0, help="máximo de mensajes a procesar (0 = todos)")
    ap.add_argument("--reiniciar", action="store_true", help="ignora el punto de reanudación y empieza de nuevo")
    args = ap.parse_args()

    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("❌ Falta DATABASE_URL (Render > Environment). No se ha tocado nada.")
    if not os.environ.get("GEMINI_API_KEY"):
        sys.exit("❌ Falta GEMINI_API_KEY (Render > Environment). No se ha tocado nada.")

    import psycopg2
    import psycopg2.extras
    conn = psycopg2.connect(url)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("SELECT count(*) AS n, count(embedding) AS con_vector, "
                "sum(length(content)) AS caracteres FROM messages WHERE length(content) >= %s",
                (MIN_CONTENT_LENGTH,))
    r = cur.fetchone()
    tokens = int((r["caracteres"] or 0) / 3.5)
    print(f"Mensajes vectorizables: {r['n']} (con vector actual: {r['con_vector']}).")
    print(f"≈ {tokens:,} tokens a procesar (≈ {r['n'] / embeddings.LOTE_MAX:.0f} peticiones por lotes).")
    if not args.aplicar:
        print("Modo prueba: no se ha escrito nada. Añade --aplicar para recalcular.")
        return

    desde = None
    if ESTADO.exists() and not args.reiniciar:
        desde = ESTADO.read_text().strip() or None
        if desde:
            print(f"↻ Reanudando desde mensajes anteriores a {desde}")

    hechos = fallos = anulados = 0
    while True:
        cur.execute(
            "SELECT id, content, created_at FROM messages "
            "WHERE length(content) >= %s AND (%s::timestamptz IS NULL OR created_at < %s::timestamptz) "
            "ORDER BY created_at DESC LIMIT %s",
            (MIN_CONTENT_LENGTH, desde, desde, embeddings.LOTE_MAX))
        filas = cur.fetchall()
        if not filas or (args.limite and hechos + fallos >= args.limite):
            break

        vectores = embeddings.embed_textos([f["content"] for f in filas])
        if not any(vectores):
            # Cuota o error: espera y reintenta el mismo lote (no avanza el punto de reanudación)
            print("⏳ Gemini no devolvió vectores (cuota o error); espero 65 s y reintento…")
            embeddings._pausado_hasta = 0.0
            time.sleep(65)
            fallos += 1
            if fallos >= 5:
                sys.exit("❌ Demasiados fallos seguidos. Reanuda más tarde con el mismo comando.")
            continue
        fallos = 0

        for f, v in zip(filas, vectores):
            if v:
                cur.execute("UPDATE messages SET embedding = %s::vector WHERE id = %s",
                            (embeddings.vector_literal(v), f["id"]))
                hechos += 1
            else:
                # Sin vector nuevo, el antiguo (de otro modelo) NO sirve: se borra para que la
                # consolidación normal lo vuelva a generar con Gemini.
                cur.execute("UPDATE messages SET embedding = NULL WHERE id = %s", (f["id"],))
                anulados += 1
        conn.commit()
        desde = filas[-1]["created_at"].isoformat()
        ESTADO.parent.mkdir(exist_ok=True)
        ESTADO.write_text(desde)
        print(f"  {hechos} mensajes vectorizados…", end="\r")
        time.sleep(1)

    ESTADO.unlink(missing_ok=True)
    print(f"\n✅ Terminado: {hechos} mensajes con vector de Gemini."
          + (f" {anulados} quedaron sin vector (los regenerará la consolidación)." if anulados else ""))


if __name__ == "__main__":
    main()
