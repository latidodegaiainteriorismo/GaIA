"""
embeddings.py

Cliente compartido para generar embeddings con la API de Gemini
(gemini-embedding-001, endpoint embedContent / batchEmbedContents).

HISTORIA: hasta el 3-nov-2026 se usaba HuggingFace Inference Providers con
paraphrase-multilingual-MiniLM-L12-v2. Se quedó sin créditos (HTTP 402) y se pasó
a Gemini, que ya se usa para el chat (misma GEMINI_API_KEY, sin cuenta nueva).

Los vectores de un modelo NO son comparables con los de otro: al cambiar de modelo
hay que recalcular los ya guardados (scripts/reembeber.py). Gemini permite elegir
las dimensiones (outputDimensionality), así que se mantienen las 384 de las
columnas vector(384) y no hay cambio de esquema. Con menos de 3072 dimensiones los
vectores NO salen normalizados; aquí se normalizan (la distancia coseno de pgvector
no lo exige, pero así los valores quedan en el rango esperado).

Gemini distingue entre «documento» y «consulta» (taskType): los textos que se guardan
se vectorizan con RETRIEVAL_DOCUMENT, y el mensaje con el que se busca con RETRIEVAL_QUERY.
"""

import logging
import math
import time
import requests
from config import GEMINI_API_KEY, GEMINI_EMBEDDING_MODEL, EMBEDDING_DIMENSIONS

logger = logging.getLogger(__name__)

_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models/"
_EMBED_URL = f"{_BASE_URL}{GEMINI_EMBEDDING_MODEL}:embedContent"
_BATCH_URL = f"{_BASE_URL}{GEMINI_EMBEDDING_MODEL}:batchEmbedContents"

# Timeout corto: esto corre DENTRO del ciclo de respuesta al usuario. Si
# Gemini tarda, preferimos seguir sin busqueda semantica (el sistema
# degrada solo a FTS) antes que hacer esperar a la persona.
_TIMEOUT_SECONDS = 8
_TIMEOUT_LOTE_SECONDS = 60

# El modelo admite ~2048 tokens de entrada; se recorta el texto por seguridad.
_MAX_CHARS = 6000

# Máximo de textos por petición batchEmbedContents (límite de la API: 100).
LOTE_MAX = 50

TAREA_DOCUMENTO = "RETRIEVAL_DOCUMENT"
TAREA_CONSULTA = "RETRIEVAL_QUERY"

# Si Gemini rechaza la clave (401/403) o dice que no hay créditos (402), insistir solo
# gasta tiempo: se desactiva la llamada un rato y se reintenta después.
# Si se agota la cuota por minuto (429) la pausa es corta.
_PAUSA_TRAS_ERROR_CUENTA_S = 1800
_PAUSA_TRAS_LIMITE_S = 60
_codigos_de_cuenta = (401, 402, 403)
_pausado_hasta = 0.0


def _pausar(segundos: float):
    global _pausado_hasta
    _pausado_hasta = time.monotonic() + segundos


def _normalizar(vector: list[float]) -> list[float]:
    norma = math.sqrt(sum(x * x for x in vector))
    return [x / norma for x in vector] if norma else vector


def _entrada(texto: str, tarea: str) -> dict:
    return {
        "model": f"models/{GEMINI_EMBEDDING_MODEL}",
        "content": {"parts": [{"text": texto.strip()[:_MAX_CHARS]}]},
        "taskType": tarea,
        "outputDimensionality": EMBEDDING_DIMENSIONS,
    }


def _vector_valido(valores) -> list[float] | None:
    if not isinstance(valores, list) or len(valores) != EMBEDDING_DIMENSIONS:
        logger.warning(
            f"[embeddings] Dimension inesperada: "
            f"{len(valores) if isinstance(valores, list) else type(valores)} "
            f"(se esperaban {EMBEDDING_DIMENSIONS})"
        )
        return None
    return _normalizar(valores)


def _gestionar_error(response) -> None:
    logger.warning(f"[embeddings] HTTP {response.status_code}: {response.text[:200]}")
    if response.status_code in _codigos_de_cuenta:
        _pausar(_PAUSA_TRAS_ERROR_CUENTA_S)
        logger.error(f"[embeddings] Problema de cuenta con Gemini (HTTP "
                     f"{response.status_code}): sin embeddings durante "
                     f"{_PAUSA_TRAS_ERROR_CUENTA_S // 60} min")
    elif response.status_code == 429:
        _pausar(_PAUSA_TRAS_LIMITE_S)
        logger.warning(f"[embeddings] Limite de cuota de Gemini: pausa de {_PAUSA_TRAS_LIMITE_S} s")


def embed_text(text: str, consulta: bool = False) -> list[float] | None:
    """
    Convierte un texto en su vector de 384 dimensiones.
    consulta=True para el mensaje con el que se BUSCA; False para lo que se guarda.
    Devuelve None si falla — quien llame DEBE funcionar igualmente sin
    vector (degradacion elegante a busqueda por texto completo).
    """
    if not GEMINI_API_KEY or not text or not text.strip():
        return None
    if time.monotonic() < _pausado_hasta:
        return None

    try:
        response = requests.post(
            _EMBED_URL,
            headers={"x-goog-api-key": GEMINI_API_KEY},
            json=_entrada(text, TAREA_CONSULTA if consulta else TAREA_DOCUMENTO),
            timeout=_TIMEOUT_SECONDS,
        )
        if response.status_code != 200:
            _gestionar_error(response)
            return None
        return _vector_valido(response.json().get("embedding", {}).get("values"))

    except Exception as e:
        logger.warning(f"[embeddings] Error generando embedding: {e}")
        return None


def embed_textos(textos: list[str]) -> list[list[float] | None]:
    """
    Vectoriza varios textos (como documentos) en UNA sola petición. Devuelve una lista
    del mismo largo que `textos`, con None en los que fallen o no quepan en el lote
    (LOTE_MAX). Pensado para recalcular en bloque (scripts/reembeber.py); en el chat
    se usa embed_text().
    """
    resultado: list[list[float] | None] = [None] * len(textos)
    posiciones = [i for i, t in enumerate(textos) if t and t.strip()][:LOTE_MAX]
    if not GEMINI_API_KEY or not posiciones or time.monotonic() < _pausado_hasta:
        return resultado

    try:
        response = requests.post(
            _BATCH_URL,
            headers={"x-goog-api-key": GEMINI_API_KEY},
            json={"requests": [_entrada(textos[i], TAREA_DOCUMENTO) for i in posiciones]},
            timeout=_TIMEOUT_LOTE_SECONDS,
        )
        if response.status_code != 200:
            _gestionar_error(response)
            return resultado
        for i, emb in zip(posiciones, response.json().get("embeddings", [])):
            resultado[i] = _vector_valido(emb.get("values"))
    except Exception as e:
        logger.warning(f"[embeddings] Error generando lote de embeddings: {e}")
    return resultado


def vector_literal(vec: list[float]) -> str:
    """
    Formatea un vector Python como literal de pgvector: '[0.1,0.2,...]'.

    6 decimales: suficiente de sobra para similitud de coseno (los valores
    de un vector normalizado estan en el rango [-1, 1], y diferencias por debajo de
    1e-6 no cambian el ranking de resultados), y mantiene el literal en
    ~3.6KB por consulta en vez de los ~4.3KB del formato de 8 decimales.
    """
    return "[" + ",".join(f"{float(x):.6f}" for x in vec) + "]"
