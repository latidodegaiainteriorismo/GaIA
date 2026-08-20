import re
import base64
import asyncio
import logging
import time
import edge_tts
from config import EDGE_TTS_VOICE

logger = logging.getLogger(__name__)

# ── Límite de texto para síntesis de voz ──────────────────────────────────────
#
# AJUSTE (19-ago-2026): subido de 1200 a 8000 chars para que GaIA lea las
# respuestas completas. 8000 cubre el máximo absoluto que puede generar
# Gemini con max_tokens=1800 (~7200 chars de texto bruto, ~6000 tras limpiar
# markdown) con un 10% de margen adicional.
#
# AVISO: el límite original de 1200 se puso tras un SIGKILL por OOM (Render
# free tier, 512MB) el 19-ago-2026 — edge_tts acumula todos los chunks de
# audio en RAM antes de devolver nada, y textos largos disparan un pico de
# memoria que puede matar el worker si coincide con otra carga pesada
# (embeddings, consolidación). Con 8000 chars ese riesgo vuelve a existir.
# Si los workers empiezan a caer de nuevo, la solución de raíz es subir
# el plan de Render a Starter (2GB RAM, ~$7/mes) en vez de volver a truncar.
_TTS_MAX_CHARS = 8000

# Límite específico para fragmentos sueltos (p.ej. "reproducir selección" en
# el frontend): el usuario selecciona texto a mano, así que no hace falta
# permitir hasta 8000 caracteres — con 1500 sobra de sobra para cualquier
# selección razonable, y evita que alguien use el endpoint /tts como
# generador de audio gratis para textos largos arbitrarios.
_TTS_FRAGMENT_MAX_CHARS = 1500

# Signos de cierre de frase considerados para cortar limpio. Se busca el
# último de estos ANTES del límite, para no cortar a mitad de frase.
_FRASE_CIERRE = re.compile(r'[.!?…]')


def _truncar_en_frase(text: str, max_chars: int) -> str:
    """
    Recorta el texto a max_chars como mucho, cortando en el último cierre de
    frase disponible antes de ese límite. Si no encuentra ningún cierre de
    frase razonable (texto sin puntuación, o el primer punto está muy al
    principio), corta en seco en max_chars como último recurso — mejor un
    corte brusco que sin límite alguno.
    """
    if len(text) <= max_chars:
        return text

    fragmento = text[:max_chars]
    cierres = [m.end() for m in _FRASE_CIERRE.finditer(fragmento)]

    # Umbral mínimo: no aceptamos un corte tan temprano que deje el audio
    # ridículamente corto (ej. si la primera frase termina a los 20
    # caracteres). Exigimos al menos el 40% del límite para considerarlo
    # un buen punto de corte.
    umbral_minimo = int(max_chars * 0.4)
    cierres_validos = [c for c in cierres if c >= umbral_minimo]

    if cierres_validos:
        return text[:cierres_validos[-1]].strip()

    # Sin un cierre de frase decente: corte duro en seco.
    return fragmento.strip()


async def _generate(text: str) -> bytes:
    communicate  = edge_tts.Communicate(text, EDGE_TTS_VOICE)
    audio_chunks = []
    async for chunk in communicate.stream():
        if chunk['type'] == 'audio':
            audio_chunks.append(chunk['data'])
    return b''.join(audio_chunks)


def text_to_speech(text: str, max_chars: int = _TTS_MAX_CHARS):
    """
    Convierte texto a audio MP3 en base64.
    Motor: Microsoft Edge TTS (es-ES-ElviraNeural por defecto).
    Sin API key. Sin coste. Sin límites de rate.

    El texto se trunca a max_chars (cortando en frase completa) antes de
    sintetizar, para evitar picos de memoria en workers con RAM ajustada.
    Esto no afecta al texto que se muestra ni se guarda — solo al audio.

    max_chars por defecto es _TTS_MAX_CHARS (respuestas normales del chat).
    Para fragmentos sueltos (selección de texto en el frontend), pásale
    _TTS_FRAGMENT_MAX_CHARS — ver text_to_speech_fragment() más abajo.

    Devuelve base64 string o None si falla.
    """
    text = (text or '').strip()
    if not text:
        return None

    # Limpiar markdown que Edge TTS leería literalmente
    text_clean = re.sub(r'[*#`_~]', '', text)
    text_clean = re.sub(r'\n{2,}', ' ', text_clean).strip()

    original_len = len(text_clean)
    if original_len > max_chars:
        text_clean = _truncar_en_frase(text_clean, max_chars)
        logger.info(
            f'[TTS] Texto truncado para audio: {original_len} → {len(text_clean)} chars'
        )

    try:
        logger.info(f'[TTS] {len(text_clean)} chars | voz={EDGE_TTS_VOICE}')
        _t0 = time.monotonic()
        loop = asyncio.new_event_loop()
        try:
            audio_bytes = loop.run_until_complete(_generate(text_clean))
        finally:
            loop.close()
        _elapsed = time.monotonic() - _t0

        if not audio_bytes:
            logger.error(f'[TTS] Audio vacío (tras {_elapsed:.1f}s)')
            return None

        logger.info(f'[TTS] ✅ {len(audio_bytes)} bytes en {_elapsed:.1f}s')
        return base64.b64encode(audio_bytes).decode('utf-8')
    except Exception as e:
        logger.error(f'[TTS] ❌ {type(e).__name__}: {str(e)[:200]}')
        return None


def text_to_speech_fragment(text: str):
    """
    Variante de text_to_speech() para fragmentos sueltos de texto ya
    existente (p.ej. "reproducir esta selección" en el frontend), con un
    límite de caracteres más corto (_TTS_FRAGMENT_MAX_CHARS) — no hace falta
    el margen de 8000 chars pensado para respuestas completas del chat.
    """
    return text_to_speech(text, max_chars=_TTS_FRAGMENT_MAX_CHARS)
