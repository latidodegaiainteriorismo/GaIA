"""
Ingesta de las transcripciones de un canal de YouTube en knowledge_chunks.

Se ejecuta EN LOCAL (desde el PC de Adrián), no en Render ni en GitHub Actions:
YouTube bloquea las IP de servidores al pedir transcripciones. Escribe directamente
en la base de datos con DATABASE_URL, igual que scripts/ingest_knowledge.py.

Los chunks se guardan con visibility='creator' (privados: solo los ven Mónica y
Adrián). Para publicarlos: UPDATE knowledge_chunks SET visibility='all' WHERE source LIKE '%(<autor> - YouTube)';

Uso (PowerShell, desde la carpeta del backend):
  pip install yt-dlp youtube-transcript-api psycopg2-binary
  python scripts/ingest_youtube.py --listar                  # solo muestra qué entraría, sin descargar
  python scripts/ingest_youtube.py --descargar               # baja y cachea transcripciones (sin tocar la BD)
  $env:DATABASE_URL = "postgresql://..."                     # la de Render > Environment
  python scripts/ingest_youtube.py --guardar                 # descarga lo que falte y lo guarda en la BD

Es idempotente: cada vídeo borra sus chunks anteriores antes de insertarse, y las
transcripciones ya descargadas se reutilizan (~/.gaia_youtube/<autor>/).
"""

import argparse
import json
import os
import random
import re
import sys
import time
import unicodedata
from pathlib import Path

CANAL_POR_DEFECTO = "https://www.youtube.com/@josemillanastrologohumanista/videos"
AUTOR_POR_DEFECTO = "José Millán"
CATEGORIA = "Astrología"

CHUNK_WORDS = 225      # ≈ 300 tokens, igual que scripts/ingest_knowledge.py
OVERLAP_WORDS = 22
CACHE_RAIZ = Path.home() / ".gaia_youtube"


# ── Filtro de contenido ───────────────────────────────────────────────────────
# Solo interesa lo práctico (lectura de carta: signos, planetas, casas, aspectos,
# elementos, configuraciones...). Se descartan los vídeos periódicos (lunaciones,
# eclipses, quincenas, previsiones por signo y mes) y la actualidad política.

def _norm(s: str) -> str:
    return unicodedata.normalize("NFKD", (s or "").lower()).encode("ascii", "ignore").decode()


PERIODICO = re.compile(
    r"luna (nueva|llena)|novilunio|plenilunio|eclipse|quincena|primera mitad|segunda mitad|primara mitad|"
    r"pronostico|clima astral|semana del|astrotweet|astrotuit|ingreso en|previsi|horoscopo|"
    r"\b(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre|diciembre)\b.{0,12}\b20\d\d|"
    r"\b20(1\d|2\d)\b|lunacion|ciclo anual|balance del ano|que nos espera|elecciones|"
    r"forecast|unfiltered|directo|en vivo|entrevista|sorteo|curso|taller|masterclass|suscrip|gracias|aniversario"
)

PRACTICO = re.compile(
    r"carta natal|en la carta|en tu carta|casa (i|ii|iii|iv|v|vi|vii|viii|ix|x|xi|xii|\d+)\b|\bcasas?\b|"
    r"aspecto|conjuncion|oposicion|cuadratura|trigono|sextil|quincuncio|"
    r"elementos?\b|fuego|tierra|aire|agua|cardinal|fijo|mutable|"
    r"ascendente|medio cielo|descendente|nodos?|quiron|lilith|"
    r"deconstructing|deconstruyendo|unboxing|unboxed|desatado|"
    r"\b(sol|luna|mercurio|venus|marte|jupiter|saturno|urano|neptuno|pluton)\b.{0,40}\b(en|casa|signo|natal|aspecto)\b|"
    r"\b(aries|tauro|geminis|cancer|leo|virgo|libra|escorpio|sagitario|capricornio|acuario|piscis)\b|"
    r"signos?\b|planetas?\b|retrogrado|transito|astrologia humanistica|que es la astrologia|como leer|interpretar|"
    r"polaridad|triplicidad|cuadruplicidad|dignidad|regente|regencia|domicilio|exaltacion"
)

# Títulos que no encajan en PRACTICO pero sí son contenido práctico
EXTRA_INCLUIR = re.compile(
    r"^(pluton|neptuno|urano|saturno|jupiter|marte|venus|mercurio) con juan carlos|transpersonales|"
    r"explicad[oa]|^los ciclos|^el ciclo|ciclos de (saturno|neptuno|urano|pluton)|"
    r"\byod\b|gran cruz|cuadrada|stellium|configuraciones|hemisferios|cuadrantes|cualidades astrologicas|"
    r"retorno solar|progresiones|carta compuesta|sinastri|malefic|retornos de saturno|"
    r"^(saturno|jupiter|mercurio|pluton|urano)[:.]|neptuno mercurio|sexo, marte|in the mood for love|"
    r"las doce lunas|esponsales de la luna|pluton el camino|crisis de los 40"
)

# El curso de introducción (Libra, partes I–III) es contenido práctico aunque diga "curso"
CURSO_PRACTICO = re.compile(r"parte (i|ii|iii)\b.*introduccion a la astrologia|libra, el otro como espejo")
TRAS_PERIODICO_FUERTE = re.compile(
    r"carta natal|en la carta|casa (i|ii|iii|iv|v|vi|vii|viii|ix|x|xi|xii)\b|en aspecto|aspectos|\bcasas?\b"
)


# Teoría de eras, actualidad y tráilers: no es lectura de carta
EXCLUIR_SIEMPRE = re.compile(
    r"eras? astrologicas?|era de (acuario|piscis)|apocalipsis|puigdemont|videoblog de astrologia|"
    r"200 anos de aire|alba de un nuevo mundo|"
    # Marcadores estructurales de los vídeos periódicos: aunque el título pegadizo
    # hable de "casa" o "carta natal", son previsiones y no se anulan.
    r"la astrologia (de|del)\b|luna (nueva|llena)|novilunio|plenilunio|eclipse|quincena|"
    r"(primera|segunda|ultima|primara) mitad|ingreso (de|en)|clima astral|semana del|"
    # Eventos concretos (conjunciones/cuadraturas de un año): previsión, no lectura de carta
    r"^la (gran )?(conjuncion|cuadratura|oposicion|semicuadratura)\b"
)


def clasificar(titulo: str) -> str:
    """Devuelve 'incluir', 'periodico' (descartado) o 'otro' (descartado: no encaja)."""
    t = _norm(titulo)
    if EXCLUIR_SIEMPRE.search(t):
        return "otro"
    if CURSO_PRACTICO.search(t):
        return "incluir"
    if PERIODICO.search(t) and not TRAS_PERIODICO_FUERTE.search(t):
        return "periodico"
    if PRACTICO.search(t) or EXTRA_INCLUIR.search(t):
        return "incluir"
    return "otro"


# ── Texto: limpieza y troceado ────────────────────────────────────────────────

def limpiar_transcripcion(segmentos) -> str:
    """Une los segmentos de subtítulos y quita marcas como [Música] o [Aplausos]."""
    texto = " ".join(s.strip() for s in segmentos if s and s.strip())
    texto = re.sub(r"\[[^\]]{1,30}\]", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def trocear(texto: str, tam: int = CHUNK_WORDS, solape: int = OVERLAP_WORDS) -> list[str]:
    palabras = texto.split()
    chunks, inicio = [], 0
    while inicio < len(palabras):
        fin = min(inicio + tam, len(palabras))
        chunks.append(" ".join(palabras[inicio:fin]))
        if fin == len(palabras):
            break
        inicio += tam - solape
    return chunks


def etiqueta_fuente(titulo: str, autor: str) -> str:
    return f"{titulo.strip()} ({autor} - YouTube)"


def preparar_chunks(titulo: str, texto: str) -> list[str]:
    """Cada chunk lleva el título delante: mejora la búsqueda por texto completo."""
    return [f"{titulo.strip()}. {c}" for c in trocear(texto)]


# ── Descarga (yt-dlp + youtube-transcript-api) ────────────────────────────────

def _slug(autor: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", _norm(autor)).strip("-")


def listar_canal(url: str, cache: Path, refrescar: bool = False) -> list[dict]:
    """Lista los vídeos del canal con sus títulos en español (se cachea 24 h)."""
    archivo = cache / "canal.json"
    if archivo.exists() and not refrescar and time.time() - archivo.stat().st_mtime < 86400:
        return json.loads(archivo.read_text(encoding="utf-8"))
    import yt_dlp
    opts = {
        "quiet": True, "no_warnings": True, "extract_flat": True, "skip_download": True,
        "extractor_args": {"youtube": {"lang": ["es"]}},
        "http_headers": {"Accept-Language": "es-ES,es;q=0.9"},
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    videos = [{"id": e["id"], "title": e.get("title") or "", "duration": e.get("duration") or 0}
              for e in info.get("entries", []) if e.get("id")]
    cache.mkdir(parents=True, exist_ok=True)
    archivo.write_text(json.dumps(videos, ensure_ascii=False), encoding="utf-8")
    return videos


class Bloqueado(Exception):
    """YouTube está bloqueando las peticiones desde esta IP."""


class ErrorTemporal(Exception):
    """Fallo de red u otro error pasajero: no significa que el vídeo no tenga subtítulos."""


def descargar_transcripcion(video_id: str, reintentos: int = 3):
    """Devuelve la lista de textos de la transcripción, o None si el vídeo no tiene.
    Lanza Bloqueado si YouTube limita la IP, o ErrorTemporal si falla por otra causa."""
    from youtube_transcript_api import YouTubeTranscriptApi
    from youtube_transcript_api import (TranscriptsDisabled, NoTranscriptFound, VideoUnavailable)
    try:
        from youtube_transcript_api import RequestBlocked, IpBlocked
        bloqueos = (RequestBlocked, IpBlocked)
    except ImportError:
        bloqueos = ()
    api = YouTubeTranscriptApi()
    for intento in range(1, reintentos + 1):
        try:
            f = api.fetch(video_id, languages=["es", "es-ES", "es-419", "es-MX"])
            return [s.text for s in f]
        except (TranscriptsDisabled, NoTranscriptFound, VideoUnavailable):
            return None
        except bloqueos as e:
            raise Bloqueado(str(e)[:200])
        except Exception as e:
            if intento == reintentos:
                raise ErrorTemporal(f"{type(e).__name__}: {str(e)[:120]}")
            time.sleep(5 * intento)
    return None


def _descargar_con_paciencia(video_id: str, espera_min: int = 20, max_esperas: int = 8):
    """Si YouTube limita la IP (429), espera y reintenta en vez de rendirse."""
    for n in range(max_esperas + 1):
        try:
            return descargar_transcripcion(video_id)
        except Bloqueado:
            if n == max_esperas:
                raise
            print(f"  ⏸️  YouTube limita las peticiones desde esta IP: espero {espera_min} min "
                  f"y reintento ({n + 1}/{max_esperas})...", flush=True)
            time.sleep(espera_min * 60)


def obtener_textos(seleccion: list[dict], cache: Path, pausa=(6.0, 12.0)) -> dict:
    """Descarga (o lee de la caché) la transcripción de cada vídeo. {id: texto}
    El ritmo es deliberadamente lento: YouTube limita la IP si se le piden muchas seguidas."""
    cache.mkdir(parents=True, exist_ok=True)
    textos, sin_subtitulos, fallos = {}, 0, 0
    for i, v in enumerate(seleccion, 1):
        archivo = cache / f"{v['id']}.txt"
        sin = cache / f"{v['id']}.sin_transcripcion"
        if archivo.exists():
            textos[v["id"]] = archivo.read_text(encoding="utf-8")
            continue
        if sin.exists():
            sin_subtitulos += 1
            continue
        try:
            segs = _descargar_con_paciencia(v["id"])
        except ErrorTemporal as e:
            fallos += 1
            print(f"  [{i}/{len(seleccion)}] ⚠️  {v['title'][:50]}: {e} (se reintentará en la próxima ejecución)", flush=True)
            continue
        if segs is None:
            sin.write_text("", encoding="utf-8")
            sin_subtitulos += 1
        else:
            texto = limpiar_transcripcion(segs)
            archivo.write_text(texto, encoding="utf-8")
            textos[v["id"]] = texto
        print(f"  [{i}/{len(seleccion)}] {v['title'][:70]}", flush=True)
        time.sleep(random.uniform(*pausa))
    if sin_subtitulos:
        print(f"ℹ️  {sin_subtitulos} vídeos sin transcripción disponible (se omiten)")
    if fallos:
        print(f"⚠️  {fallos} vídeos fallaron por errores pasajeros: vuelve a lanzar el script para completarlos")
    return textos


# ── Base de datos ─────────────────────────────────────────────────────────────

def guardar(seleccion: list[dict], textos: dict, autor: str, visibilidad: str = "creator") -> int:
    import psycopg2
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("❌ Falta DATABASE_URL (Render > Environment). No se ha escrito nada.")
    conn = psycopg2.connect(url)
    cur = conn.cursor()
    total = 0
    for v in seleccion:
        texto = textos.get(v["id"])
        if not texto:
            continue
        fuente = etiqueta_fuente(v["title"], autor)
        chunks = preparar_chunks(v["title"], texto)
        cur.execute("DELETE FROM knowledge_chunks WHERE source = %s", (fuente,))
        for c in chunks:
            cur.execute(
                "INSERT INTO knowledge_chunks (source, category, content, visibility) "
                "VALUES (%s, %s, %s, %s)",
                (fuente, CATEGORIA, c, visibilidad),
            )
        conn.commit()
        total += len(chunks)
    cur.close()
    conn.close()
    return total


# ── Programa principal ────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--canal", default=CANAL_POR_DEFECTO)
    ap.add_argument("--autor", default=AUTOR_POR_DEFECTO)
    ap.add_argument("--listar", action="store_true", help="muestra qué entraría y qué no")
    ap.add_argument("--descargar", action="store_true", help="descarga y cachea transcripciones (sin BD)")
    ap.add_argument("--guardar", action="store_true", help="descarga lo que falte y guarda en la BD")
    ap.add_argument("--limite", type=int, default=0, help="solo los N primeros (para probar)")
    ap.add_argument("--refrescar", action="store_true", help="vuelve a listar el canal ignorando la caché")
    args = ap.parse_args()

    cache = CACHE_RAIZ / _slug(args.autor)
    videos = listar_canal(args.canal, cache, args.refrescar)
    grupos = {"incluir": [], "periodico": [], "otro": []}
    for v in videos:
        grupos[clasificar(v["title"])].append(v)
    horas = lambda xs: sum(x["duration"] for x in xs) / 3600
    print(f"Canal: {len(videos)} vídeos → incluir {len(grupos['incluir'])} ({horas(grupos['incluir']):.0f} h) | "
          f"periódicos {len(grupos['periodico'])} | otros {len(grupos['otro'])}")

    seleccion = grupos["incluir"][: args.limite] if args.limite else grupos["incluir"]
    if args.listar:
        for clave in ("incluir", "otro"):
            print(f"\n=== {clave.upper()} ({len(grupos[clave])}) ===")
            for v in grupos[clave]:
                print(f"  {int(v['duration']/60):3d} min | {v['title']}")
        return
    if not (args.descargar or args.guardar):
        ap.print_help()
        return

    textos = obtener_textos(seleccion, cache)
    palabras = sum(len(t.split()) for t in textos.values())
    print(f"✅ {len(textos)} transcripciones disponibles ({palabras:,} palabras ≈ {palabras // CHUNK_WORDS:,} fragmentos)")
    if args.guardar:
        n = guardar(seleccion, textos, args.autor)
        print(f"🎉 {n:,} fragmentos guardados en knowledge_chunks (visibility='creator')")


if __name__ == "__main__":
    try:
        main()
    except Bloqueado as e:
        sys.exit(f"🚫 YouTube está bloqueando las peticiones desde esta IP: {e}\n"
                 "Espera un rato (o cambia de red) y vuelve a lanzar: lo ya descargado no se repite.")
