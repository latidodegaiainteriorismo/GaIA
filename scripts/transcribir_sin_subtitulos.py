"""
Transcribe con Whisper (Groq) los vídeos de un canal cuyos subtítulos están desactivados.

Por qué: ingest_youtube.py solo puede leer los subtítulos que YouTube publica. De los 474
vídeos prácticos de José Millán, 48 (≈ 9,6 h) los tienen desactivados; entre ellos varios
de método (p. ej. «Los tres niveles de expresión en la interpretación de un tránsito»).
Este script baja SOLO el audio, lo comprime y lo transcribe con whisper-large-v3 de Groq
(gratis, literal: no resume), y deja el texto en la misma caché que usa ingest_youtube.py,
de modo que `ingest_youtube.py --guardar` lo incluye sin más.

Necesita: yt-dlp, ffmpeg en el PATH, la librería groq y GROQ_API_KEY en el entorno
(la misma de Render > Environment; no la pegues en el chat).

Uso (desde la raíz del backend):
    python scripts/transcribir_sin_subtitulos.py --listar             # qué falta y cuánto dura; no hace nada más
    python scripts/transcribir_sin_subtitulos.py --limite 10          # los 10 más prioritarios (método primero)
    python scripts/transcribir_sin_subtitulos.py                      # todos los pendientes (reanudable)

Groq limita las horas de audio por hora y por día en el nivel gratuito: si se agota, el script
se detiene con un aviso y se reanuda al día siguiente con el mismo comando.
"""
import argparse
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ingest_youtube import CACHE_RAIZ, limpiar_transcripcion, prioridad  # noqa: E402

MODELO = "whisper-large-v3"
MAX_BYTES = 24 * 1024 * 1024           # límite de archivo de Groq (nivel gratuito: 25 MB)
TROZO_SEGUNDOS = 1200                  # si el audio comprimido no cabe, se parte en trozos de 20 min

# Frases que Whisper inventa cuando oye silencio o música
_ALUCINACIONES = re.compile(
    r"(subt[ií]tulos? (realizados? )?por la comunidad de amara\.org|suscr[ií]bete al canal|gracias por ver el v[ií]deo)",
    re.IGNORECASE)


def pendientes(cache: Path) -> list[dict]:
    """Vídeos sin subtítulos y sin transcripción propia, los de método primero."""
    canal = json.loads((cache / "canal.json").read_text(encoding="utf-8"))
    videos = canal if isinstance(canal, list) else canal.get("videos", canal.get("entries", []))
    por_id = {v["id"]: v for v in videos}
    ids = [f.name[:-len(".sin_transcripcion")] for f in cache.glob("*.sin_transcripcion")
           if not (cache / f"{f.name[:-len('.sin_transcripcion')]}.txt").exists()]
    lista = [por_id[i] for i in ids if i in por_id]
    return sorted(lista, key=lambda v: (prioridad(v["title"]), v["title"]))


def bajar_audio(video_id: str, destino: Path) -> Path:
    import yt_dlp
    opciones = {"format": "bestaudio/best", "outtmpl": str(destino / "%(id)s.%(ext)s"),
                "quiet": True, "no_warnings": True, "noprogress": True}
    with yt_dlp.YoutubeDL(opciones) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=True)
        return Path(ydl.prepare_filename(info))


def comprimir(origen: Path, destino: Path) -> list[Path]:
    """Mono, 16 kHz, 32 kbps: lo que necesita Whisper y unos 4 KB/s. Devuelve 1 o más archivos."""
    mp3 = destino / f"{origen.stem}.mp3"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(origen), "-vn", "-ac", "1",
                    "-ar", "16000", "-b:a", "32k", str(mp3)], check=True)
    if mp3.stat().st_size <= MAX_BYTES:
        return [mp3]
    patron = destino / f"{origen.stem}_%03d.mp3"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp3), "-f", "segment",
                    "-segment_time", str(TROZO_SEGUNDOS), "-c", "copy", str(patron)], check=True)
    return sorted(destino.glob(f"{origen.stem}_*.mp3"))


class LimiteDiario(Exception):
    pass


def transcribir(cliente, archivo: Path, reintentos: int = 3) -> str:
    import groq
    for intento in range(1, reintentos + 1):
        try:
            texto = cliente.audio.transcriptions.create(
                model=MODELO, file=(archivo.name, archivo.read_bytes()),
                language="es", response_format="text", temperature=0)
            return texto if isinstance(texto, str) else getattr(texto, "text", "")
        except groq.RateLimitError as e:
            mensaje = str(e)
            if re.search(r"per day|ASPD|daily", mensaje, re.IGNORECASE):
                raise LimiteDiario(mensaje[:200])
            print(f"   ⏳ límite por hora de Groq; espero 70 s (intento {intento}/{reintentos})", flush=True)
            time.sleep(70)
    raise RuntimeError("Groq sigue limitando tras varios intentos")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--canal", default="jose-millan", help="carpeta de la caché (por defecto jose-millan)")
    ap.add_argument("--listar", action="store_true", help="solo muestra los pendientes y la duración total")
    ap.add_argument("--limite", type=int, default=0, help="máximo de vídeos en esta ejecución (0 = todos)")
    ap.add_argument("--pausa", type=float, nargs=2, default=(20.0, 40.0), metavar=("MIN", "MAX"),
                    help="segundos de espera entre vídeos para no saturar a YouTube (por defecto 20 40)")
    args = ap.parse_args()

    cache = CACHE_RAIZ / args.canal
    lista = pendientes(cache)
    horas = sum(v.get("duration") or 0 for v in lista) / 3600
    print(f"{len(lista)} vídeos sin subtítulos pendientes de transcribir (≈ {horas:.1f} h de audio).")
    if args.listar:
        for v in lista:
            print(f"  {round((v.get('duration') or 0) / 60):>3} min  {v['title']}")
        return
    if not lista:
        return
    if not os.environ.get("GROQ_API_KEY"):
        sys.exit("❌ Falta GROQ_API_KEY en el entorno (Render > Environment). No se ha hecho nada.")

    import groq
    cliente = groq.Groq()
    hechos = 0
    for i, v in enumerate(lista, 1):
        if args.limite and hechos >= args.limite:
            break
        print(f"[{i}/{len(lista)}] {v['title'][:70]}", flush=True)
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            try:
                crudo = bajar_audio(v["id"], tmp)
                trozos = comprimir(crudo, tmp)
                texto = " ".join(transcribir(cliente, t) for t in trozos)
            except LimiteDiario as e:
                print(f"⛔ Groq alcanzó el límite diario de audio. Reanuda mañana con el mismo comando.\n   {e}")
                break
            except Exception as e:
                print(f"   ⚠️  {type(e).__name__}: {str(e)[:160]} (se reintentará en la próxima ejecución)")
                if re.search(r"429|bot|Sign in|HTTP Error 403", str(e)):
                    print("   YouTube parece limitar esta IP: cambia de red (modo avión) y reanuda.")
                    break
                continue
        texto = _ALUCINACIONES.sub(" ", texto)
        texto = limpiar_transcripcion([texto])
        if len(texto.split()) < 30:
            print("   ⚠️  transcripción casi vacía; se descarta")
            continue
        (cache / f"{v['id']}.txt").write_text(texto, encoding="utf-8")
        (cache / f"{v['id']}.whisper").write_text("", encoding="utf-8")     # marca: texto de Whisper, no de YouTube
        hechos += 1
        print(f"   ✅ {len(texto.split())} palabras", flush=True)
        time.sleep(random.uniform(*args.pausa))

    restantes = len(pendientes(cache))
    print(f"\nTranscritos en esta ejecución: {hechos}. Pendientes: {restantes}.")
    if hechos:
        print("Siguiente paso: python scripts/ingest_youtube.py --guardar (con DATABASE_URL en el entorno).")


if __name__ == "__main__":
    main()
