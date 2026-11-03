"""
Recalcula las cartas natales guardadas en user_birth_charts con el motor actual.

Por qué: hasta nov-2026 las posiciones planetarias se calculaban contra la eclíptica del año
2000 (≈0,2-0,4° bajas) y con el observador en la superficie de la Tierra (la Luna se
desplazaba hasta ~1°). Las cartas ya guardadas conservan esos valores. Este script las
recalcula con la fecha, hora y lugar que cada una ya tiene guardados.

Uso (desde la raíz del backend, con DATABASE_URL en el entorno):
    python scripts/recalcular_cartas.py              # solo muestra qué cambiaría (no escribe)
    python scripts/recalcular_cartas.py --aplicar    # guarda copia de seguridad y actualiza

La copia de seguridad (JSON con todas las filas) se escribe en ~/.gaia_backup/ — fuera del
repositorio, porque contiene datos personales. Nunca la subas a GitHub.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from astro_core.birth_chart import calculate_birth_chart  # noqa: E402


def _cambios(viejo: dict, nuevo: dict) -> list[str]:
    """Descripción corta de lo que cambia de signo (lo que más importa) y del Ascendente."""
    texto = []
    viejos = {p["name"]: p for p in viejo.get("planets", [])}
    for p in nuevo["planets"]:
        v = viejos.get(p["name"])
        if v and v["sign"] != p["sign"]:
            texto.append(f"{p['name']}: {v['sign']} {v['degree_in_sign']:.2f}° → {p['sign']} {p['degree_in_sign']:.2f}°")
    va, na = viejo.get("ascendant"), nuevo.get("ascendant")
    if va and na and va["sign"] != na["sign"]:
        texto.append(f"ascendente: {va['sign']} → {na['sign']}")
    return texto


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--aplicar", action="store_true", help="escribe los cambios (si no, solo los muestra)")
    args = ap.parse_args()

    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("❌ Falta DATABASE_URL (Render > Environment). No se ha tocado nada.")

    import psycopg2
    import psycopg2.extras
    conn = psycopg2.connect(url)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT user_id, person_label, relationship, birth_datetime_utc, birth_lat, birth_lon, "
                "birth_place, sun_sign, moon_sign, rising_sign, full_chart FROM user_birth_charts "
                "ORDER BY created_at")
    filas = cur.fetchall()
    print(f"{len(filas)} cartas guardadas.")

    if args.aplicar and filas:
        carpeta = Path.home() / ".gaia_backup"
        carpeta.mkdir(exist_ok=True)
        destino = carpeta / f"cartas_{datetime.now():%Y%m%d_%H%M%S}.json"
        destino.write_text(json.dumps(filas, default=str, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"💾 Copia de seguridad: {destino}")

    actualizadas = 0
    for f in filas:
        chart_viejo = f["full_chart"] if isinstance(f["full_chart"], dict) else json.loads(f["full_chart"])
        time_known = chart_viejo.get("time_known", True)
        dt = f["birth_datetime_utc"]
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        chart = calculate_birth_chart(dt, f["birth_lat"], f["birth_lon"], f["birth_place"])
        chart.time_known = time_known
        nuevo = chart.to_dict()

        etiqueta = f"{str(f['user_id'])[:8]}… «{f['person_label']}»"
        cambios = _cambios(chart_viejo, nuevo)
        print(f"- {etiqueta}: {'; '.join(cambios) if cambios else 'sin cambios de signo'}"
              f"{'' if time_known else '  (sin hora)'}")

        if args.aplicar:
            sol = chart.get_planet("sun")
            luna = chart.get_planet("moon")
            cur.execute(
                "UPDATE user_birth_charts SET full_chart = %s, sun_sign = %s, moon_sign = %s, "
                "rising_sign = %s, updated_at = NOW() WHERE user_id = %s AND person_label = %s",
                (json.dumps(nuevo), sol.sign if sol else None, luna.sign if luna else None,
                 nuevo["ascendant"]["sign"] if time_known and nuevo["ascendant"] else None,
                 f["user_id"], f["person_label"]),
            )
            actualizadas += 1

    if args.aplicar:
        conn.commit()
        print(f"✅ {actualizadas} cartas recalculadas.")
    else:
        conn.rollback()
        print("\nNo se ha escrito nada. Para aplicar: python scripts/recalcular_cartas.py --aplicar")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
