import re
import base64
import asyncio
import logging
import edge_tts
from config import EDGE_TTS_VOICE

logger = logging.getLogger(__name__)

async def _generate(text: str) -> bytes:
    communicate  = edge_tts.Communicate(text, EDGE_TTS_VOICE)
    audio_chunks = []
    async for chunk in communicate.stream():
        if chunk['type'] == 'audio':
            audio_chunks.append(chunk['data'])
    return b''.join(audio_chunks)

def text_to_speech(text: str):
    """
    Convierte texto a audio MP3 en base64.
    Motor: Microsoft Edge TTS (es-ES-ElviraNeural por defecto).
    Sin API key. Sin coste. Sin límites de rate.
    Devuelve base64 string o None si falla.
    """
    text = (text or '').strip()
    if not text:
        return None

    # Limpiar markdown que Edge TTS leería literalmente
    text_clean = re.sub(r'[*#`_~]', '', text)
    text_clean = re.sub(r'\n{2,}', ' ', text_clean).strip()

    try:
        logger.info(f'[TTS] {len(text_clean)} chars | voz={EDGE_TTS_VOICE}')
        loop = asyncio.new_event_loop()
        try:
            audio_bytes = loop.run_until_complete(_generate(text_clean))
        finally:
            loop.close()

        if not audio_bytes:
            logger.error('[TTS] Audio vacío')
            return None

        logger.info(f'[TTS] ✅ {len(audio_bytes)} bytes')
        return base64.b64encode(audio_bytes).decode('utf-8')

    except Exception as e:
        logger.error(f'[TTS] ❌ {type(e).__name__}: {str(e)[:200]}')
        return None
