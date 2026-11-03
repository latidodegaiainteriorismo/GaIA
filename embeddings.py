"""
embeddings.py

Cliente compartido para generar embeddings via la API de HuggingFace
Inference Providers (router.huggingface.co).

CONTEXTO (14-ago-2026): durante mucho tiempo se asumio que Render no podia
llamar a HuggingFace por un problema de DNS. El diagnostico en vivo mostro
que el fallo real era doble: (1) el endpoint antiguo, api-inference.
huggingface.co, esta OFICIALMENTE RETIRADO por HuggingFace (410 Gone) desde
finales de 2025, sustituido por router.huggingface.co; y (2) el token de
HUGGINGFACE_API_KEY no tenia marcado el permiso "Make calls to Inference
Providers". Corregidos ambos, la llamada funciona con normalidad desde
Render — no hay bloqueo de red real.

Se usa el mismo modelo que la ingesta de Knowledge
(sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2, 384
dimensiones), asi que estos embeddings son directamente comparables a los
de knowledge_chunks si algun dia hiciera falta cruzar ambos espacios.
"""

import logging
import time
import requests
from config import HUGGINGFACE_API_KEY, HUGGINGFACE_MODEL, EMBEDDING_DIMENSIONS

logger = logging.getLogger(__name__)

_EMBED_URL = (
    f"https://router.huggingface.co/hf-inference/models/{HUGGINGFACE_MODEL}"
    f"/pipeline/feature-extraction"
)

# Timeout corto: esto corre DENTRO del ciclo de respuesta al usuario. Si
# HuggingFace tarda, preferimos seguir sin busqueda semantica (el sistema
# degrada solo a FTS) antes que hacer esperar a la persona.
_TIMEOUT_SECONDS = 8

# Si HuggingFace dice que no hay créditos (402) o rechaza la clave (401/403),
# insistir solo gasta tiempo: la consolidación hacía 25 llamadas fallidas cada
# pocos minutos (~10 s con la única petición del worker bloqueada). Se desactiva
# la llamada un rato y se reintenta después, por si ya se han añadido créditos.
_PAUSA_TRAS_ERROR_CUENTA_S = 1800
_codigos_de_cuenta = (401, 402, 403)
_pausado_hasta = 0.0


def embed_text(text: str) -> list[float] | None:
    """
    Convierte un texto en su vector de 384 dimensiones.
    Devuelve None si falla — quien llame DEBE funcionar igualmente sin
    vector (degradacion elegante a busqueda por texto completo).
    """
    global _pausado_hasta
    if not HUGGINGFACE_API_KEY or not text or not text.strip():
        return None
    if time.monotonic() < _pausado_hasta:
        return None

    try:
        response = requests.post(
            _EMBED_URL,
            headers={"Authorization": f"Bearer {HUGGINGFACE_API_KEY}"},
            json={"inputs": text.strip()},
            timeout=_TIMEOUT_SECONDS,
        )
        if response.status_code != 200:
            logger.warning(f"[embeddings] HTTP {response.status_code}: {response.text[:200]}")
            if response.status_code in _codigos_de_cuenta:
                _pausado_hasta = time.monotonic() + _PAUSA_TRAS_ERROR_CUENTA_S
                logger.error(f"[embeddings] Problema de cuenta con HuggingFace (HTTP "
                             f"{response.status_code}): sin embeddings durante "
                             f"{_PAUSA_TRAS_ERROR_CUENTA_S // 60} min")
            return None

        vector = response.json()

        # El endpoint puede devolver [float, ...] para un texto suelto, o
        # [[float, ...]] si interpreta la entrada como lote. Normalizamos.
        if isinstance(vector, list) and vector and isinstance(vector[0], list):
            vector = vector[0]

        if not isinstance(vector, list) or len(vector) != EMBEDDING_DIMENSIONS:
            logger.warning(
                f"[embeddings] Dimension inesperada: "
                f"{len(vector) if isinstance(vector, list) else type(vector)} "
                f"(se esperaban {EMBEDDING_DIMENSIONS})"
            )
            return None

        return vector

    except Exception as e:
        logger.warning(f"[embeddings] Error generando embedding: {e}")
        return None


def vector_literal(vec: list[float]) -> str:
    """
    Formatea un vector Python como literal de pgvector: '[0.1,0.2,...]'.

    6 decimales: suficiente de sobra para similitud de coseno (los valores
    de este modelo estan en el rango [-1, 1], y diferencias por debajo de
    1e-6 no cambian el ranking de resultados), y mantiene el literal en
    ~3.6KB por consulta en vez de los ~4.3KB del formato de 8 decimales.
    """
    return "[" + ",".join(f"{float(x):.6f}" for x in vec) + "]"
