"""
query_router.py

Catálogo de documentos de GaIA + función de enrutamiento por LLM.
La búsqueda es por DOCUMENTO (source), no por carpeta — así no depende
de cómo estén organizadas las subcarpetas en knowledge/.

Mónica Martos y la Enciclopedia de la Biología NO están aquí: se buscan
siempre, de forma incondicional, directamente en knowledge.py (código,
no decisión del LLM).
"""

import json
import logging
from groq import Groq
from config import GROQ_API_KEY, GROQ_MODEL

logger = logging.getLogger(__name__)
_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

# ── Catálogo de documentos ────────────────────────────────────────────────────
# Clave: fragmento distintivo que debe aparecer en el nombre de archivo (source)
# Valor: para qué sirve el documento, en lenguaje natural, para que Groq decida
#        cuándo es relevante.
DOCUMENT_CATALOG = {
    "Kybalion": (
        "Principios herméticos y leyes universales: mentalismo, correspondencia, "
        "vibración, polaridad, ritmo, causa-efecto, género cósmico. Esqueleto "
        "técnico de cómo funciona el universo a nivel metafísico."
    ),
    "Milagros": (  # Un Curso de Milagros
        "Perdón, diferencia entre ego y amor, percepción, milagros, culpa, "
        "relaciones especiales vs relaciones santas, sanación mental y espiritual."
    ),
    "Holgazanes": (  # Iluminación para Holgazanes
        "No-resistencia, aceptación radical, niveles de consciencia, amor "
        "incondicional sin esfuerzo ni lucha espiritual forzada."
    ),
    "Poder del Ahora": (
        "Presencia, el ahora, identificación con la mente pensante, el cuerpo "
        "del dolor, el ego, quietud interior, desidentificación del pensamiento."
    ),
    "Libro del Conocimiento": (
        "Texto canalizado por Vedia Bülent Önsü, presentado como mensaje cósmico "
        "recibido de una fuente espiritual superior a partir de los años 80. "
        "Se plantea como guía para una nueva era de la humanidad, con vínculos "
        "simbólicos a Rumi/Mevlana. Trata sobre la conexión entre la consciencia "
        "individual y una totalidad cósmica superior, leyes universales "
        "transmitidas como mensajes celestiales, y el papel de la humanidad en "
        "una transición espiritual planetaria."
    ),
    "Matías De Stefano": (
        "Memoria akáshica, historia de la humanidad y civilizaciones antiguas, "
        "razas estelares, propósito planetario, activación de memoria celular, "
        "Cosmogénesis."
    ),
    "Argüelles": (  # incluye Factor Maya y otras obras del autor
        "Calendario maya, Ley del Tiempo, sincronario de 13 Lunas / 28 días, "
        "Tzolkin, convergencia armónica, arte y consciencia planetaria."
    ),
    "Biodescodificación": (
        "Significado emocional de síntomas físicos y enfermedades, conflictos "
        "biológicos, shocks emocionales, lenguaje simbólico del cuerpo."
    ),
    "Bioneuroemoción": (
        "Relación entre emociones no resueltas, patrones familiares/sistémicos "
        "y síntomas físicos. Complementario a Biodescodificación."
    ),
    "Feng Shui": (
        "Energía del espacio habitado, Chi, elementos, Bagua, orientaciones "
        "favorables, armonización del hogar."
    ),
    "Geobiología": (
        "Energías telúricas del terreno, redes Hartmann y Curry, geopatías, "
        "impacto del entorno físico y geológico en la salud."
    ),
    "Astrología Occidental": (
        "Signos zodiacales, casas astrológicas, planetas, tránsitos, carta natal, "
        "tradición astrológica occidental/tropical."
    ),
    "Astrología China": (
        "Animales del zodiaco chino, elementos (agua, madera, fuego, tierra, "
        "metal), ciclos de 12 años, compatibilidades."
    ),
    "Astrología Maya": (
        "Tzolkin, sellos solares, tonos galácticos, sincronario maya "
        "(relacionado con la obra de José Argüelles)."
    ),
    "Adrián Lozano": (
        "Era de Acuario y astrología, crecimiento personal, diferencia entre el "
        "yo personal y el Ser, funcionamiento de la mente consciente e "
        "inconsciente, aclaraciones y matices propios sobre \"Iluminación para "
        "Holgazanes\" y \"Un Curso de Milagros\"."
    ),
}


def _build_catalog_text() -> str:
    lines = []
    for titulo, descripcion in DOCUMENT_CATALOG.items():
        lines.append(f'- "{titulo}": {descripcion}')
    return "\n".join(lines)


def preprocess_query(message: str) -> dict:
    """
    Le pide a Groq que interprete la pregunta del usuario y decida:
    - qué documentos del catálogo son relevantes (por su clave exacta)
    - palabras clave expandidas para mejorar el FTS
    - si hace falta recurrir a la web porque el conocimiento propio no basta

    Devuelve dict con: {"documents": [...], "keywords": [...], "needs_web": bool}
    En caso de error, devuelve un fallback seguro (sin documentos extra, sin web).
    """
    if not _client:
        return {"documents": [], "keywords": [message], "needs_web": False}

    catalog_text = _build_catalog_text()

    prompt = f"""Eres el módulo de enrutamiento de conocimiento de GaIA.
Tu única tarea es decidir qué documentos de este catálogo son relevantes
para responder la pregunta del usuario, y si hace falta buscar en la web.

CATÁLOGO DE DOCUMENTOS DISPONIBLES:
{catalog_text}

REGLAS:
- Puedes elegir 0, 1 o varios documentos si la pregunta combina temas (por
  ejemplo, una pregunta sobre "por qué me duele la espalda y qué dice mi
  carta astral" puede activar Biodescodificación + Astrología Occidental).
- Usa la clave EXACTA del catálogo tal cual aparece entre comillas.
- Si la pregunta es puramente conversacional/emocional sin relación con
  ningún documento, devuelve una lista vacía en "documents".
- Marca "needs_web": true SOLO si la pregunta requiere información factual
  actual o externa que ningún documento del catálogo podría cubrir
  (ejemplo: eventos astronómicos de hoy, noticias, datos técnicos externos).
  Para preguntas espirituales, existenciales o de crecimiento personal,
  needs_web siempre debe ser false, aunque no haya documento que encaje.
- "keywords": 7-8 palabras o conceptos clave relacionados con la pregunta, en
  español. IMPORTANTE: no te limites a repetir literalmente las palabras de
  la pregunta del usuario — piensa en cómo podría estar redactado el
  contenido real de un documento sobre este tema (sinónimos, términos
  técnicos relacionados, conceptos asociados). La búsqueda encuentra
  fragmentos que contengan CUALQUIERA de estas palabras (no todas a la vez),
  así que cuantos más ángulos distintos cubras, mejor será la búsqueda.
  Ejemplo: para "háblame de la era de Acuario" → ["era de acuario", "acuario",
  "nueva era", "cambio de era", "transición planetaria", "consciencia
  colectiva", "astrología", "humanidad"]

Responde ÚNICAMENTE con JSON válido, sin texto adicional, sin markdown:
{{"documents": ["..."], "keywords": ["...", "..."], "needs_web": false}}

Pregunta del usuario: {message}"""

    try:
        response = _client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
            temperature=0.2,
        )
        raw = response.choices[0].message.content.strip()
        # Limpieza por si Groq envuelve en markdown pese a la instrucción
        raw = raw.replace("```json", "").replace("```", "").strip()
        parsed = json.loads(raw)

        result = {
            "documents": parsed.get("documents", []) or [],
            "keywords": parsed.get("keywords", []) or [message],
            "needs_web": bool(parsed.get("needs_web", False)),
        }
        logger.info(f"[router] docs={result['documents']} web={result['needs_web']}")
        return result

    except Exception as e:
        logger.warning(f"[router] Fallback por error: {e}")
        return {"documents": [], "keywords": [message], "needs_web": False}
