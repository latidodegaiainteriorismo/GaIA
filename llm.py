import os
import re
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from groq import Groq, APIStatusError
from config import GROQ_API_KEY, GROQ_MODELS_GENERAL, GROQ_MODELS_ASTROLOGY

logger = logging.getLogger(__name__)

# Cliente Groq (singleton)
_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

# Retrocompatibilidad con código que importe estos nombres directamente
GROQ_MODEL          = GROQ_MODELS_GENERAL[0]
GROQ_MODEL_FALLBACK = GROQ_MODELS_GENERAL[1]

# Ruta al ADN — relativa al directorio de ejecución (raíz del proyecto)
_DNA_PATH           = os.path.join(os.path.dirname(__file__), 'prompts', 'gaia_dna.txt')
_DNA_ASTROLOGY_PATH = os.path.join(os.path.dirname(__file__), 'prompts', 'gaia_dna_astrologia.txt')

# Códigos de error de Groq que significan "esta petición no cabe en la cuota
# de este modelo ahora mismo" — en ambos casos tiene sentido saltar al
# siguiente modelo de la cadena en vez de fallar directamente.
_RETRYABLE_STATUS_CODES = (429, 413)

# Zona horaria de referencia para GaIA — Adrián y Mónica operan desde
# España, y es la zona horaria que tiene sentido mostrarle al usuario en
# la conversación salvo que en el futuro se quiera personalizar por usuario.
_APP_TIMEZONE = "Europe/Madrid"

_DIAS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MESES_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
             "septiembre", "octubre", "noviembre", "diciembre"]


class GroqRateLimitError(Exception):
    """Se lanza cuando TODOS los modelos de la cadena están agotados/rate-limited.
    Lleva el tiempo de espera estimado (en segundos) si se pudo extraer del
    mensaje de error de Groq, para poder mostrárselo al usuario."""
    def __init__(self, message: str, retry_after_seconds=None):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


def _current_datetime_block() -> str:
    """
    Construye el bloque de fecha/hora actual para inyectar en el system prompt.

    Por qué existe: un LLM no tiene reloj interno — sin este bloque, GaIA no
    tiene forma de saber qué día es hoy, y puede alucinar fechas si el usuario
    pregunta "¿qué día es hoy?", "cuánto falta para mi cumpleaños", etc.
    Se evita depender del locale del sistema (puede no estar configurado en
    el servidor) escribiendo los nombres de día/mes en español a mano — mismo
    criterio que _PLANET_ES en astrology.py.
    """
    now = datetime.now(ZoneInfo(_APP_TIMEZONE))
    dia_semana = _DIAS_ES[now.weekday()]
    mes = _MESES_ES[now.month - 1]
    return (
        f"## FECHA Y HORA ACTUAL\n"
        f"Hoy es {dia_semana}, {now.day} de {mes} de {now.year}. Son las "
        f"{now.strftime('%H:%M')} (hora peninsular española). Usa esta fecha "
        f"como referencia real si el usuario pregunta por el día, la hora, "
        f"cuánto falta para algo, o menciona 'hoy'/'mañana'/'ayer'.\n\n"
    )


def load_dna() -> str:
    """Lee el ADN de GaIA desde archivo. Cacheable en Fase 1."""
    try:
        with open(_DNA_PATH, 'r', encoding='utf-8') as f:
            return f.read().strip()
    except FileNotFoundError:
        logger.error(f'[LLM] ADN no encontrado en {_DNA_PATH}')
        return ''


def load_dna_astrologia() -> str:
    """
    Lee el ADN especializado en astrología (interpretación de cartas, tránsitos,
    tono riguroso vs conversacional). Si no existe el archivo, se degrada con
    normalidad: la carta técnica igualmente funciona, solo sin ese refinamiento.
    """
    try:
        with open(_DNA_ASTROLOGY_PATH, 'r', encoding='utf-8') as f:
            return f.read().strip()
    except FileNotFoundError:
        logger.warning(f'[LLM] ADN de astrología no encontrado en {_DNA_ASTROLOGY_PATH} — se usa solo el ADN base')
        return ''


def save_dna(new_content: str) -> bool:
    """Sobrescribe el ADN principal de GaIA. Usado por el modo desarrollador."""
    try:
        with open(_DNA_PATH, 'w', encoding='utf-8') as f:
            f.write(new_content.strip() + '\n')
        logger.info(f'[LLM] ADN actualizado ({len(new_content)} chars)')
        return True
    except Exception as e:
        logger.error(f'[LLM] Error guardando ADN: {e}')
        return False


def save_dna_astrologia(new_content: str) -> bool:
    """Sobrescribe el ADN de astrología. Usado por el modo desarrollador."""
    try:
        with open(_DNA_ASTROLOGY_PATH, 'w', encoding='utf-8') as f:
            f.write(new_content.strip() + '\n')
        logger.info(f'[LLM] ADN de astrología actualizado ({len(new_content)} chars)')
        return True
    except Exception as e:
        logger.error(f'[LLM] Error guardando ADN de astrología: {e}')
        return False


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


def _call_model(model: str, messages: list, max_tokens: int = 1024, temperature: float = 0.8) -> str:
    completion = _client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    return completion.choices[0].message.content


def _call_with_fallback_chain(models: list, messages: list, max_tokens: int = 1024,
                               temperature: float = 0.8) -> str:
    """
    Prueba cada modelo de la lista en orden. Salta al siguiente si el actual
    devuelve 429 (rate-limited) o 413 (payload/tokens por minuto excedidos).
    Cualquier otro tipo de error se propaga inmediatamente, sin reintentos.
    """
    if not _client:
        raise RuntimeError('Groq client no inicializado — revisa GROQ_API_KEY')

    last_error = None
    for i, model in enumerate(models):
        try:
            response = _call_model(model, messages, max_tokens, temperature)
            if i > 0:
                logger.info(f'[LLM] ✅ Respuesta vía fallback #{i}: {model}')
            return response
        except APIStatusError as e:
            if e.status_code not in _RETRYABLE_STATUS_CODES:
                raise
            logger.warning(f'[LLM] {model} devolvió {e.status_code}, probando siguiente modelo de la cadena')
            last_error = e
            continue

    retry_after = _parse_retry_seconds(str(last_error)) if last_error else None
    logger.error(f'[LLM] Todos los modelos de la cadena agotados: {models}. Retry en {retry_after}s')
    raise GroqRateLimitError(
        f'Todos los modelos disponibles están saturados ({", ".join(models)})',
        retry_after_seconds=retry_after
    )


def call_groq(history: list, cross_memory: str = '', knowledge_context: str = '',
              astrology_context: str = '', extra_system_prefix: str = '') -> str:
    """
    Llama a Groq (chat general) con el historial de la conversación, probando
    la cadena de modelos GROQ_MODELS_GENERAL en orden ante saturación de cuota.

    Args:
        history:              Lista de dicts {'role': 'user'|'assistant', 'content': str}
        cross_memory:         Contexto adicional de conversaciones anteriores (FASE 0)
        knowledge_context:    Chunks relevantes recuperados por RAG (FASE 2)
        astrology_context:    Carta natal + tránsitos activos del usuario, si los tiene
        extra_system_prefix:  Texto adicional a anteponer al ADN (ej. contexto de
                               desarrollador) — puede venir vacío ('')
    Returns:
        Respuesta de GaIA como string.
    Raises:
        GroqRateLimitError: si todos los modelos de la cadena están saturados.
    """
    dna    = load_dna()
    system = (_current_datetime_block() + extra_system_prefix + dna + cross_memory +
              '\n\n' + _build_knowledge_block(knowledge_context))
    if astrology_context:
        system += '\n\n' + astrology_context

    messages = [{'role': 'system', 'content': system}]
    for m in history:
        role = m['role'] if m['role'] in ('user', 'assistant') else 'user'
        messages.append({'role': role, 'content': m['content']})

    logger.info(f'[LLM] Llamando Groq (general) | msgs={len(messages)} | '
               f'model={GROQ_MODELS_GENERAL[0]} | rag={bool(knowledge_context)}')

    response = _call_with_fallback_chain(GROQ_MODELS_GENERAL, messages)
    logger.info(f'[LLM] ✅ Respuesta: {len(response)} chars')
    return response


def call_groq_astrology(user_message: str, astrology_context: str) -> str:
    """
    Llama a Groq usando la cadena de modelos dedicada a astrología
    (GROQ_MODELS_ASTROLOGY) y el ADN especializado en interpretación de
    cartas/tránsitos. Pensado para consultas puramente astrológicas donde se
    prioriza rigor y precisión en la exposición de datos sobre fluidez
    conversacional — de ahí el modelo más pequeño y económico por defecto.

    Args:
        user_message:      Mensaje del usuario (la pregunta astrológica)
        astrology_context: Carta natal + tránsitos ya formateados (astrology.py)
    Returns:
        Respuesta de GaIA como string.
    Raises:
        GroqRateLimitError: si todos los modelos de la cadena están saturados.
    """
    dna_base      = load_dna()
    dna_astrology = load_dna_astrologia()
    system = _current_datetime_block() + dna_base
    if dna_astrology:
        system += '\n\n' + dna_astrology
    if astrology_context:
        system += '\n\n' + astrology_context

    messages = [
        {'role': 'system', 'content': system},
        {'role': 'user', 'content': user_message},
    ]

    logger.info(f'[LLM] Llamando Groq (astrología) | model={GROQ_MODELS_ASTROLOGY[0]}')

    response = _call_with_fallback_chain(GROQ_MODELS_ASTROLOGY, messages)
    logger.info(f'[LLM] ✅ Respuesta astrología: {len(response)} chars')
    return response
