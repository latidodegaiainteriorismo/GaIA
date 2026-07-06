"""
user_profile.py

Gestiona el perfil personal del usuario (nombre preferido, familia, gustos,
estudios, profesión...) para personalizar la experiencia con GaIA:
  - Onboarding: GaIA pregunta UNA SOLA VEZ por usuario qué desea compartir.
  - Detección: si el usuario menciona un dato personal nuevo en conversación
    normal, GaIA pregunta si quiere guardarlo (sin repetir la pregunta si ya
    se le hizo esa misma sugerencia antes en la misma sesión, ver chat.py).
  - El perfil vive en su propia tabla (user_profiles), separado de auth.
"""

import json
import logging
from db import db_one, db_run

logger = logging.getLogger(__name__)

# Categorías de referencia para orientar a GaIA sobre qué tipo de datos son
# relevantes de detectar y ofrecer guardar. No es una lista cerrada ni
# validada por código — es contexto que se pasa al LLM para que use su
# propio criterio, y es ampliable editando este archivo (igual que el DNA).
REFERENCE_CATEGORIES = [
    "nombre por el que prefiere que le llamen (si es distinto al de su cuenta)",
    "familia: pareja, hijos, padres, hermanos — nombres y relación",
    "gustos y aficiones",
    "estudios o formación",
    "profesión u ocupación",
    "lugar donde vive",
    "mascotas",
]


def get_profile(user_id: str) -> dict | None:
    """Devuelve el perfil del usuario (dict con preferred_name, profile_data,
    onboarding_completed), o None si aún no existe fila para este usuario."""
    row = db_one(
        "SELECT preferred_name, profile_data, onboarding_completed FROM user_profiles WHERE user_id = %s",
        (user_id,)
    )
    if not row:
        return None
    data = row["profile_data"]
    if isinstance(data, str):
        data = json.loads(data)
    return {
        "preferred_name": row["preferred_name"],
        "profile_data": data or {},
        "onboarding_completed": row["onboarding_completed"],
    }


def ensure_profile_row(user_id: str) -> dict:
    """
    Garantiza que existe una fila de perfil para este usuario (la crea vacía
    si no existe). Devuelve el perfil resultante. Se llama en cada login /
    primer mensaje para poder comprobar onboarding_completed con seguridad.
    """
    profile = get_profile(user_id)
    if profile is not None:
        return profile

    db_run(
        "INSERT INTO user_profiles (user_id, profile_data, onboarding_completed) "
        "VALUES (%s, %s, %s) ON CONFLICT (user_id) DO NOTHING",
        (user_id, json.dumps({}), False)
    )
    return {"preferred_name": None, "profile_data": {}, "onboarding_completed": False}


def needs_onboarding(user_id: str) -> bool:
    """True si a este usuario todavía no se le ha preguntado qué compartir."""
    profile = ensure_profile_row(user_id)
    return not profile["onboarding_completed"]


def mark_onboarding_completed(user_id: str):
    """Marca que ya se le hizo la pregunta de onboarding a este usuario
    (independientemente de si respondió algo o decidió no compartir nada —
    la pregunta solo se hace una vez, según lo pedido)."""
    db_run(
        "UPDATE user_profiles SET onboarding_completed = TRUE, updated_at = NOW() WHERE user_id = %s",
        (user_id,)
    )


def update_profile_data(user_id: str, updates: dict, preferred_name: str = None) -> bool:
    """
    Fusiona nuevos datos en el perfil existente (no lo sobrescribe entero).
    Args:
        updates: dict con las claves/valores nuevos a fusionar en profile_data
        preferred_name: si se proporciona, actualiza también ese campo
    """
    profile = ensure_profile_row(user_id)
    merged = {**profile["profile_data"], **updates}
    name_to_save = preferred_name if preferred_name is not None else profile["preferred_name"]

    db_run(
        "UPDATE user_profiles SET profile_data = %s, preferred_name = %s, updated_at = NOW() WHERE user_id = %s",
        (json.dumps(merged), name_to_save, user_id)
    )
    logger.info(f"[profile] Perfil actualizado para user={user_id}: claves nuevas {list(updates.keys())}")
    return True


def format_profile_context(user_id: str) -> str:
    """
    Construye el bloque de texto con el perfil del usuario para inyectar en
    el system prompt. Devuelve '' si el usuario no ha compartido nada aún.
    """
    profile = get_profile(user_id)
    if not profile or (not profile["preferred_name"] and not profile["profile_data"]):
        return ""

    lines = ["## PERFIL DEL USUARIO (compartido voluntariamente, úsalo con naturalidad)"]
    if profile["preferred_name"]:
        lines.append(f"- Prefiere que le llames: {profile['preferred_name']}")
    for key, value in profile["profile_data"].items():
        lines.append(f"- {key}: {value}")

    lines.append(
        "\nUsa estos datos con la misma naturalidad con la que un amigo recuerda cosas de ti — "
        "no los recites como una ficha, intégralos solo cuando aporten calidez o contexto real "
        "a la conversación."
    )
    return "\n".join(lines)


def format_onboarding_prompt_instruction() -> str:
    """
    Instrucción para que GaIA, en su próxima respuesta, incluya la pregunta
    de onboarding de forma natural (una sola vez, la primera interacción).
    """
    categories = "; ".join(REFERENCE_CATEGORIES)
    return (
        "\n## PRIMERA VEZ CON ESTE USUARIO — PREGUNTA DE ONBOARDING\n"
        "Esta es la primera conversación real con este usuario. Después de responder a lo que "
        "te ha dicho, aprovecha para preguntarle — con calidez, sin sonar a formulario — si "
        "quiere contarte algo sobre sí mismo para que puedas acompañarle mejor: cómo prefiere "
        f"que le llames, o cosas como {categories}. "
        "Deja muy claro que es totalmente opcional, que comparta solo lo que le apetezca, y que "
        "es únicamente para mejorar cómo le acompañas — nunca lo presiones ni lo repitas si "
        "no responde a esto ahora; esta pregunta no debe volver a aparecer en el futuro.\n"
    )


def format_new_data_detected_instruction(detected_summary: str) -> str:
    """
    Instrucción para que GaIA pregunte si quiere guardar un dato personal
    nuevo que acaba de mencionar en la conversación (detectado por el propio
    LLM en su respuesta, ver chat.py para el flujo completo).
    """
    return (
        f"\n## DATO PERSONAL NUEVO DETECTADO: {detected_summary}\n"
        "El usuario acaba de mencionar este dato sobre sí mismo y no está guardado en su perfil. "
        "Pregúntale con naturalidad, en un solo momento breve, si quiere que lo recuerdes para "
        "futuras conversaciones. No lo conviertas en el foco de la respuesta — responde primero "
        "a lo que te ha preguntado, y añade la pregunta después, como algo secundario.\n"
    )


# ── Detección de datos personales nuevos en el mensaje ──────────────────

def detect_new_personal_data(user_id: str, message: str) -> str | None:
    """
    Revisa si el mensaje del usuario menciona un dato personal (de las
    categorías de REFERENCE_CATEGORIES o similares, a criterio del modelo)
    que no está ya guardado en su perfil. Usa el modelo pequeño de Groq,
    con un filtro rápido previo para no gastar tokens en cada mensaje.

    Returns:
        Un resumen breve del dato detectado (ej. "su hija se llama Elena"),
        o None si no se detecta nada nuevo relevante.
    """
    # Filtro rápido: solo vale la pena consultar al LLM si el mensaje tiene
    # pinta de mencionar algo personal (evita coste en la mayoría de turnos
    # técnicos/neutros de la conversación).
    hint_words = ["mi hij", "mi pareja", "mi marido", "mi mujer", "mi novi", "mi madre",
                  "mi padre", "mi hermano", "mi hermana", "me llamo", "trabajo de",
                  "trabajo como", "soy ", "estudio", "estudié", "estudie", "vivo en",
                  "mi perro", "mi gato", "me gusta", "me encanta", "mi profesión",
                  "mi trabajo"]
    msg_lower = message.lower()
    if not any(w in msg_lower for w in hint_words):
        return None

    from llm import _client, GROQ_MODEL_FALLBACK
    if not _client:
        return None

    profile = get_profile(user_id)
    existing_data = json.dumps(profile["profile_data"], ensure_ascii=False) if profile else "{}"

    try:
        response = _client.chat.completions.create(
            model=GROQ_MODEL_FALLBACK,
            messages=[{
                "role": "system",
                "content": (
                    "Analizas un mensaje para detectar SI menciona un dato personal nuevo "
                    "sobre quien escribe (nombre preferido, familia, gustos, estudios, "
                    "profesión, lugar donde vive, mascotas, o similar) que NO esté ya en "
                    f"estos datos guardados: {existing_data}. "
                    "Si detectas un dato nuevo relevante, responde ÚNICAMENTE con un resumen "
                    "breve en tercera persona (ej. 'su hija se llama Elena', 'trabaja como "
                    "arquitecto'). Si no hay ningún dato nuevo relevante, responde "
                    "ÚNICAMENTE con la palabra NONE. No expliques tu razonamiento."
                )
            }, {
                "role": "user",
                "content": message
            }],
            temperature=0,
            max_tokens=40,
        )
        result = response.choices[0].message.content.strip()
        if result.upper() == "NONE" or not result:
            return None
        return result
    except Exception as e:
        logger.warning(f"[profile] No se pudo analizar el mensaje para datos personales: {e}")
        return None


# ── Marca de guardado en la respuesta de GaIA ───────────────────────────

import re as _re

_SAVE_MARK_PATTERN = _re.compile(r'\[GUARDAR_PERFIL:\s*([^\]]+)\]', _re.IGNORECASE)


def extract_and_apply_save_marks(user_id: str, gaia_response: str) -> str:
    """
    Busca marcas [GUARDAR_PERFIL: clave=valor] en la respuesta de GaIA
    (ver prompts/gaia_dna.txt, sección 'CÓMO GUARDO DATOS PERSONALES'),
    las aplica al perfil del usuario, y devuelve el texto SIN esas marcas
    (son un mecanismo interno, invisibles para el usuario final).

    Formato esperado dentro de la marca: "clave=valor" — si la clave es
    literalmente "nombre_preferido", se guarda como preferred_name; el
    resto de claves se guardan libremente en profile_data.

    Returns:
        El texto de gaia_response ya limpio de marcas.
    """
    matches = _SAVE_MARK_PATTERN.findall(gaia_response)
    if not matches:
        return gaia_response

    updates = {}
    preferred_name = None
    for raw in matches:
        if '=' not in raw:
            continue
        key, _, value = raw.partition('=')
        key, value = key.strip(), value.strip()
        if not key or not value:
            continue
        if key.lower() == 'nombre_preferido':
            preferred_name = value
        else:
            updates[key] = value

    if updates or preferred_name:
        update_profile_data(user_id, updates, preferred_name)
        logger.info(f"[profile] Guardado desde marca en respuesta: user={user_id} "
                   f"nombre={preferred_name} claves={list(updates.keys())}")

    return _SAVE_MARK_PATTERN.sub('', gaia_response).strip()
