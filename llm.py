import os
import re
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from openai import OpenAI, APIStatusError
from groq import Groq
from config import (
    GEMINI_API_KEY, GEMINI_BASE_URL, GEMINI_MODEL_GENERAL, GEMINI_MODEL_ASTROLOGY,
    GROQ_API_KEY, GROQ_MODELS_GENERAL, GROQ_MODELS_ASTROLOGY,
)

logger = logging.getLogger(__name__)

# ── Clientes ───────────────────────────────────────────────────────────────────
#
# MIGRACIÓN (19-ago-2026): Gemini es ahora el proveedor principal, llamado a
# través de su endpoint compatible con la API de OpenAI — por eso el cliente
# es `OpenAI(...)`, no un SDK de Google. Groq se mantiene como fallback de
# emergencia (ver _call_with_fallback_chain): si Gemini devuelve error, se
# reintenta con la cadena de Groq antes de fallar del todo. Esto es
# deliberadamente distinto del patrón anterior (varios modelos Groq en
# cadena) — ahora es "proveedor principal robusto" + "red de seguridad",
# no varios modelos compitiendo por la misma cuota estrecha.
_client = OpenAI(api_key=GEMINI_API_KEY, base_url=GEMINI_BASE_URL) if GEMINI_API_KEY else None
_groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

# Retrocompatibilidad con código que importe estos nombres directamente
GROQ_MODEL          = GEMINI_MODEL_GENERAL
GROQ_MODEL_FALLBACK = GEMINI_MODEL_GENERAL

# Ruta al ADN — relativa al directorio de ejecución (raíz del proyecto)
_DNA_PATH           = os.path.join(os.path.dirname(__file__), 'prompts', 'gaia_dna.txt')
_DNA_ASTROLOGY_PATH = os.path.join(os.path.dirname(__file__), 'prompts', 'gaia_dna_astrologia.txt')

# Códigos de error que significan "esta petición no cabe en la cuota de este
# proveedor ahora mismo" — tiene sentido pasar al fallback en vez de fallar.
_RETRYABLE_STATUS_CODES = (429, 413)

# Zona horaria de referencia para GaIA — Adrián y Mónica operan desde
# España, y es la zona horaria que tiene sentido mostrarle al usuario en
# la conversación salvo que en el futuro se quiera personalizar por usuario.
_APP_TIMEZONE = "Europe/Madrid"

_DIAS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MESES_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
             "septiembre", "octubre", "noviembre", "diciembre"]


class GroqRateLimitError(Exception):
    """Se lanza cuando Gemini Y el fallback de Groq están agotados/rate-limited.
    Se mantiene el nombre por retrocompatibilidad con routes/chat.py, que ya
    captura esta excepción explícitamente — cambiar el nombre obligaría a
    tocar ese fichero también sin ganar nada. Lleva el tiempo de espera
    estimado (en segundos) si se pudo extraer del mensaje de error, para
    poder mostrárselo al usuario."""
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
    """Extrae el tiempo de espera de un mensaje de error, ej. '33m10.656s'."""
    m = re.search(r'try again in\s+(?:(\d+)m)?(\d+(?:\.\d+)?)s', error_message, re.IGNORECASE)
    if not m:
        return None
    minutes = int(m.group(1)) if m.group(1) else 0
    seconds = float(m.group(2))
    return int(minutes * 60 + seconds)


def _call_gemini(model: str, messages: list, max_tokens: int = 1024, temperature: float = 0.8) -> str:
    completion = _client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    return completion.choices[0].message.content


def _call_groq_fallback(models: list, messages: list, max_tokens: int, temperature: float):
    """
    Red de seguridad si Gemini falla del todo. Recorre la cadena de modelos
    Groq como se hacía antes de la migración. Si esto también falla, se
    propaga GroqRateLimitError igual que antes.
    """
    if not _groq_client:
        return None
    for model in models:
        try:
            completion = _groq_client.chat.completions.create(
                model=model, messages=messages, max_tokens=max_tokens, temperature=temperature,
            )
            logger.warning(f'[LLM] ⚠️ Gemini falló — respuesta servida vía fallback Groq ({model})')
            return completion.choices[0].message.content
        except Exception as e:
            logger.warning(f'[LLM] Fallback Groq {model} también falló: {e}')
            continue
    return None


def _call_with_fallback(gemini_model: str, groq_models: list, messages: list,
                         max_tokens: int = 1024, temperature: float = 0.8) -> str:
    """
    Llama primero a Gemini. Si devuelve error retryable (429/413) o cualquier
    excepción, recurre a la cadena de Groq como red de seguridad. Solo si
    ambos proveedores fallan se propaga GroqRateLimitError.
    """
    if not _client:
        raise RuntimeError('Cliente Gemini no inicializado — revisa GEMINI_API_KEY')

    last_error = None
    try:
        return _call_gemini(gemini_model, messages, max_tokens, temperature)
    except APIStatusError as e:
        last_error = e
        logger.warning(f'[LLM] Gemini devolvió {e.status_code} — probando fallback Groq')
    except Exception as e:
        last_error = e
        logger.warning(f'[LLM] Gemini falló ({type(e).__name__}) — probando fallback Groq')

    fallback_response = _call_groq_fallback(groq_models, messages, max_tokens, temperature)
    if fallback_response is not None:
        return fallback_response

    retry_after = _parse_retry_seconds(str(last_error)) if last_error else None
    logger.error(f'[LLM] Gemini y fallback Groq agotados. Retry en {retry_after}s')
    raise GroqRateLimitError(
        'Gemini y el proveedor de respaldo están saturados o no disponibles',
        retry_after_seconds=retry_after
    )


def call_groq(history: list, cross_memory: str = '', knowledge_context: str = '',
              astrology_context: str = '', extra_system_prefix: str = '') -> str:
    """
    Llama al LLM principal (Gemini, con fallback a Groq) con el historial de
    la conversación. El nombre de la función se mantiene por retrocompatibilidad
    con routes/chat.py — cambiar el nombre no aporta nada y obligaría a tocar
    más ficheros.

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
        GroqRateLimitError: si Gemini y el fallback de Groq fallan ambos.
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

    logger.info(f'[LLM] Llamando Gemini (general) | msgs={len(messages)} | '
               f'model={GEMINI_MODEL_GENERAL} | rag={bool(knowledge_context)}')

    response = _call_with_fallback(GEMINI_MODEL_GENERAL, GROQ_MODELS_GENERAL, messages)
    logger.info(f'[LLM] ✅ Respuesta: {len(response)} chars')
    return response


def call_groq_astrology(user_message: str, astrology_context: str) -> str:
    """
    Llama al LLM (Gemini, con fallback a Groq) usando el ADN especializado en
    interpretación de cartas/tránsitos. Pensado para consultas puramente
    astrológicas donde se prioriza rigor y precisión en la exposición de
    datos sobre fluidez conversacional.

    Args:
        user_message:      Mensaje del usuario (la pregunta astrológica)
        astrology_context: Carta natal + tránsitos ya formateados (astrology.py)
    Returns:
        Respuesta de GaIA como string.
    Raises:
        GroqRateLimitError: si Gemini y el fallback de Groq fallan ambos.
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

    logger.info(f'[LLM] Llamando Gemini (astrología) | model={GEMINI_MODEL_ASTROLOGY}')

    response = _call_with_fallback(GEMINI_MODEL_ASTROLOGY, GROQ_MODELS_ASTROLOGY, messages)
    logger.info(f'[LLM] ✅ Respuesta astrología: {len(response)} chars')
    return response
