import os
import re
import logging
from groq import Groq, APIStatusError
from config import GROQ_API_KEY, GROQ_MODEL

logger = logging.getLogger(__name__)

# Cliente Groq (singleton)
_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

# Modelo más pequeño como fallback cuando el principal está rate-limited —
# tiene su propia cuota de tokens/día separada de llama-3.3-70b-versatile.
GROQ_MODEL_FALLBACK = 'llama-3.1-8b-instant'

# Ruta al ADN — relativa al directorio de ejecución (raíz del proyecto)
_DNA_PATH = os.path.join(os.path.dirname(__file__), 'prompts', 'gaia_dna.txt')


class GroqRateLimitError(Exception):
    """Se lanza cuando Groq devuelve 429 en el modelo principal Y en el fallback.
    Lleva el tiempo de espera estimado (en segundos) si se pudo extraer del
    mensaje de error de Groq, para poder mostrárselo al usuario."""
    def __init__(self, message: str, retry_after_seconds=None):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


def load_dna() -> str:
    """Lee el ADN de GaIA desde archivo. Cacheable en Fase 1."""
    try:
        with open(_DNA_PATH, 'r', encoding='utf-8') as f:
            return f.read().strip()
    except FileNotFoundError:
        logger.error(f'[LLM] ADN no encontrado en {_DNA_PATH}')
        return ''


def _build_knowledge_block(knowledge_context: str) -> str:
    """
    Construye el bloque de contexto recuperado SIEMPRE marcado explícitamente,
    esté vacío o no — así el modelo sabe con certeza en qué modo está en cada
    turno, en vez de tener que inferirlo (ver prompts/gaia_dna.txt,
    sección 'CÓMO USAR EL CONTEXTO RECUPERADO').
    """
    header = '## CONTEXTO RECUPERADO DE TU KNOWLEDGE PARA ESTA PREGUNTA'
    if knowledge_context:
        return f'{header}\n{knowledge_context}'
    return f'{header}\n(vacío — no se encontró nada relevante en tu Knowledge sobre esta pregunta concreta)'


def _parse_retry_seconds(error_message: str):
    """Extrae el tiempo de espera del mensaje de error de Groq, ej. '33m10.656s'."""
    m = re.search(r'try again in\s+(?:(\d+)m)?(\d+(?:\.\d+)?)s', error_message, re.IGNORECASE)
    if not m:
        return None
    minutes = int(m.group(1)) if m.group(1) else 0
    seconds = float(m.group(2))
    return int(minutes * 60 + seconds)


def _call_model(model: str, messages: list) -> str:
    completion = _client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=1024,
        temperature=0.8,
    )
    return completion.choices[0].message.content


def call_groq(history: list, cross_memory: str = '', knowledge_context: str = '') -> str:
    """
    Llama a Groq con el historial de la conversación.
    Args:
        history:           Lista de dicts {'role': 'user'|'assistant', 'content': str}
        cross_memory:      Contexto adicional de conversaciones anteriores (FASE 0)
        knowledge_context: Chunks relevantes recuperados por RAG (FASE 2), ya
                            filtrados por relevancia y priorizados por carpeta
                            en knowledge.py — puede venir vacío ('')
    Returns:
        Respuesta de GaIA como string.
    Raises:
        GroqRateLimitError: si tanto el modelo principal como el fallback
                             están rate-limited — con el tiempo de espera si
                             se pudo determinar, para mostrárselo al usuario.
    """
    if not _client:
        raise RuntimeError('Groq client no inicializado — revisa GROQ_API_KEY')

    dna    = load_dna()
    system = dna + cross_memory + '\n\n' + _build_knowledge_block(knowledge_context)

    messages = [{'role': 'system', 'content': system}]

    for m in history:
        role = m['role'] if m['role'] in ('user', 'assistant') else 'user'
        messages.append({'role': role, 'content': m['content']})

    logger.info(f'[LLM] Llamando Groq | msgs={len(messages)} | model={GROQ_MODEL} | rag={bool(knowledge_context)}')

    try:
        response = _call_model(GROQ_MODEL, messages)
    except APIStatusError as e:
        if e.status_code != 429:
            raise
        logger.warning(f'[LLM] {GROQ_MODEL} rate-limited, probando fallback {GROQ_MODEL_FALLBACK}')
        try:
            response = _call_model(GROQ_MODEL_FALLBACK, messages)
            logger.info(f'[LLM] ✅ Respuesta vía fallback {GROQ_MODEL_FALLBACK}')
        except APIStatusError as e2:
            if e2.status_code != 429:
                raise
            retry_after = _parse_retry_seconds(str(e2)) or _parse_retry_seconds(str(e))
            logger.error(f'[LLM] Rate limit también en fallback. Retry en {retry_after}s')
            raise GroqRateLimitError(
                'Ambos modelos de Groq están rate-limited', retry_after_seconds=retry_after
            )
        return response

    logger.info(f'[LLM] ✅ Respuesta: {len(response)} chars')
    return response
