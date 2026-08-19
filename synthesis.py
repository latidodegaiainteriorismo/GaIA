"""
synthesis.py

Síntesis viva del usuario (Fase 1 del sistema de memoria de GaIA).

Es un texto compacto (~400-500 tokens) por usuario que responde a la
pregunta "¿quién es esta persona?" — sus relaciones clave, sus patrones
emocionales, sus procesos abiertos, los hilos recientes de su vida. Se
inyecta SIEMPRE en el contexto de GaIA (como el perfil), y es lo que hace
que GaIA "te conozca" desde el primer mensaje de cualquier conversación,
sin depender de que ninguna búsqueda acierte.

A diferencia de user_profile.py (que guarda DATOS DUROS confirmados: nombre,
hija, profesión), la síntesis guarda NARRATIVA y PATRONES: "tiende a ceder
ante figuras de autoridad y luego se arrepiente", "vive un duelo por su
padre", "está decidiendo si dejar su trabajo". El perfil alimenta la
síntesis, no la sustituye — son complementarios.

La síntesis NO se genera en el ciclo de respuesta (añadiría latencia). Se
regenera de forma ASÍNCRONA desde el endpoint /maintenance/consolidate
(ver routes/maintenance.py), que UptimeRobot pinguea cada cierto tiempo —
el mismo patrón del keep-alive que ya usa el proyecto. Así, para el usuario,
la síntesis está siempre lista y actualizada sin coste de latencia.

Fase 2 extenderá la consolidación para generar también embeddings de los
mensajes (memoria episódica). Fase 1 solo hace la síntesis.
"""

import logging
from db import db_all, db_one, db_run

logger = logging.getLogger(__name__)

# Material de conversación que se pasa al LLM para generar la síntesis. Se
# limita por caracteres para no exceder el TPM de Groq (8K/min en los
# modelos actuales): ~10.000 caracteres ≈ 2.800 tokens de entrada, dejando
# margen para el razonamiento del modelo y la síntesis de salida dentro del
# mismo minuto/llamada.
_MAX_MATERIAL_CHARS = 10000

# Cuántos mensajes recientes revisar como mucho al construir el material
# (tope duro, además del límite por caracteres).
_MAX_MATERIAL_MESSAGES = 80

# Longitud objetivo de la síntesis, comunicada al modelo. No es un límite
# duro (no se trunca), es una guía para que quepa holgada en el contexto.
_SYNTHESIS_TARGET_TOKENS = 450


def get_synthesis(user_id: str) -> str | None:
    """Devuelve el texto de síntesis viva del usuario, o None si no existe aún."""
    row = db_one(
        "SELECT synthesis_text FROM user_synthesis WHERE user_id = %s",
        (user_id,)
    )
    if not row or not row.get("synthesis_text"):
        return None
    return row["synthesis_text"]


def format_synthesis_context(user_id: str) -> str:
    """
    Bloque de síntesis viva para inyectar SIEMPRE en el system prompt de GaIA.
    Devuelve '' si el usuario todavía no tiene síntesis generada (usuario
    nuevo, o antes de la primera pasada de consolidación).
    """
    text = get_synthesis(user_id)
    if not text:
        return ""
    return (
        "\n## LO QUE SABES DE ESTA PERSONA (tu comprensión viva de quién es)\n"
        "Esto es lo que has ido conociendo de esta persona a lo largo de vuestras "
        "conversaciones. Úsalo con la naturalidad de quien de verdad conoce a "
        "alguien — para entender mejor lo que te cuenta, conectar lo de hoy con lo "
        "de antes, y acompañarle desde ese conocimiento. No lo recites ni se lo "
        "leas de vuelta como una ficha; simplemente, deja que informe cómo le "
        "acompañas.\n\n"
        f"{text}\n---\n"
    )


def _gather_recent_material(user_id: str, since_iso: str | None) -> str:
    """
    Reúne el material de conversación reciente del usuario para alimentar la
    síntesis. Si since_iso está dado (consolidaciones sucesivas), solo trae
    mensajes posteriores a esa fecha; si es None (primera vez), trae los
    mensajes más recientes en general. Limitado por número y por caracteres.
    """
    if since_iso:
        rows = db_all(
            "SELECT role, content, created_at FROM messages "
            "WHERE user_id = %s AND created_at > %s "
            "ORDER BY created_at DESC LIMIT %s",
            (user_id, since_iso, _MAX_MATERIAL_MESSAGES)
        )
    else:
        rows = db_all(
            "SELECT role, content, created_at FROM messages "
            "WHERE user_id = %s "
            "ORDER BY created_at DESC LIMIT %s",
            (user_id, _MAX_MATERIAL_MESSAGES)
        )

    if not rows:
        return ""

    # Vienen en orden descendente (más nuevos primero); los invertimos para
    # leerlos en orden cronológico, que es como tienen sentido narrativo.
    rows = list(reversed(rows))

    lines = []
    total = 0
    for r in rows:
        speaker = "Usuario" if r["role"] == "user" else "GaIA"
        line = f"{speaker}: {r['content']}"
        total += len(line)
        if total > _MAX_MATERIAL_CHARS:
            break
        lines.append(line)

    return "\n".join(lines)


def _get_profile_facts(user_id: str) -> str:
    """
    Trae los datos duros del perfil (user_profiles) como texto compacto, para
    que la síntesis los tenga en cuenta. Importación diferida para evitar
    dependencia circular con user_profile (que no importa este módulo, pero
    por prudencia).
    """
    try:
        from user_profile import get_profile
        profile = get_profile(user_id)
        if not profile:
            return ""
        parts = []
        if profile.get("preferred_name"):
            parts.append(f"Prefiere que le llamen: {profile['preferred_name']}")
        for k, v in (profile.get("profile_data") or {}).items():
            parts.append(f"{k}: {v}")
        return "\n".join(parts)
    except Exception as e:
        logger.warning(f"[synthesis] No se pudo leer el perfil: {e}")
        return ""


def _build_synthesis_prompt(existing: str | None, profile_facts: str, material: str) -> str:
    """Construye el prompt que genera/actualiza la síntesis viva."""
    existing_block = (
        f"SÍNTESIS ACTUAL (actualízala integrando lo nuevo, sin perder lo que "
        f"sigue siendo cierto):\n{existing}\n"
        if existing else
        "SÍNTESIS ACTUAL: (todavía no hay — es la primera vez que sintetizas a "
        "esta persona)\n"
    )
    profile_block = (
        f"\nDATOS CONFIRMADOS DEL PERFIL (hechos que la persona ha compartido "
        f"explícitamente — dales prioridad como verdad establecida):\n{profile_facts}\n"
        if profile_facts else ""
    )

    return f"""Eres el proceso de consolidación de memoria de GaIA — algo así como lo que
hace la mente humana mientras duerme: tomar lo vivido y destilarlo en una
comprensión viva de quién es esta persona.

Tu tarea es escribir (o actualizar) una síntesis breve y densa de esta
persona, a partir de sus conversaciones recientes con GaIA. No es un resumen
de las conversaciones — es un retrato de QUIÉN ES, que le sirva a GaIA para
acompañarla mejor y conectar lo que cuenta hoy con lo que ya sabe de ella.

{existing_block}{profile_block}
CONVERSACIONES RECIENTES A INTEGRAR:
{material}

Escribe la síntesis actualizada en español, con estas secciones (usa estos
títulos exactos, y si una sección no tiene contenido aún, escribe "—"):

QUIÉN ES: contexto vital, situación actual, cómo se presenta.
RELACIONES CLAVE: personas importantes en su vida y la carga emocional de
cada vínculo (no solo quién es, sino qué representa emocionalmente).
PATRONES Y TEMAS RECURRENTES: dinámicas emocionales o relacionales que se
repiten, formas características de reaccionar, heridas o búsquedas de fondo.
Esta sección es la más valiosa: es lo que permite conectar situaciones
distintas que comparten un mismo patrón.
PROCESOS ABIERTOS: decisiones que está tomando, duelos, cambios en curso,
cosas sin resolver que siguen vivas para ella.
HILOS RECIENTES: temas concretos de los últimos días que convendría retomar
proactivamente ("¿cómo fue la reunión?", "¿qué tal con tu madre?").

REGLAS:
- Máximo ~{_SYNTHESIS_TARGET_TOKENS} palabras en total. Denso, no exhaustivo.
- Escribe solo lo que se sostiene en lo que la persona ha contado — nunca
  inventes ni especules sobre su psicología más allá de lo que muestra.
- Integra lo nuevo con lo anterior; si algo de la síntesis previa ya no
  encaja o quedó superado, actualízalo.
- Responde ÚNICAMENTE con la síntesis, sin preámbulos ni comentarios."""


def _call_llm_for_synthesis(prompt: str) -> str | None:
    """
    Llama al LLM para generar la síntesis, recorriendo la cadena de modelos
    (igual que la llamada principal del chat) hasta conseguir una respuesta.

    NOTA (14-ago-2026): la primera versión de esta función solo intentaba
    gpt-oss-120b sin cadena de fallback, y sin límite propio de timeout ni
    de reintentos. Cuando ese modelo devolvió rate-limit, el cliente de Groq
    entró en su espera interna de reintento (backoff exponencial), que se
    alargó más que el timeout por defecto de gunicorn (30s) — gunicorn mató
    el worker a media espera (SIGABRT/SIGKILL), y la petición HTTP devolvió
    500 sin ningún log de error "limpio" de la propia función.

    CORRECCIÓN (19-ago-2026, tras migración a Gemini): antes se recorrían
    los 3 modelos de GROQ_MODELS_GENERAL para no depender de que el primero
    tuviera cupo libre en Groq. Tras la migración, _client (importado de
    llm.py) apunta a Gemini, pero esta función seguía pidiendo nombres de
    modelo de Groq (ej. 'openai/gpt-oss-120b') contra ese cliente — cada
    llamada fallaba con "modelo no encontrado", y como el error se
    capturaba silenciosamente para "probar el siguiente", los tres intentos
    fallaban sin ningún aviso claro; la síntesis dejaba de actualizarse.
    Corregido a usar el modelo Gemini configurado. Ya no hace falta iterar
    varios modelos del mismo proveedor — Gemini tiene mucho más margen de
    TPM que Groq, así que un único intento con reintento vía with_options
    basta; se conserva el bucle solo como red de seguridad por si Gemini
    devuelve contenido vacío en un intento puntual.
    """
    from llm import _client
    from config import GEMINI_MODEL_GENERAL
    if not _client:
        return None

    for intento in range(2):
        try:
            response = _client.with_options(max_retries=1, timeout=25.0).chat.completions.create(
                model=GEMINI_MODEL_GENERAL,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1500,        # razonamiento + ~450 palabras de síntesis
                temperature=0.4,
                reasoning_effort="medium",
            )
            text = response.choices[0].message.content.strip()
            if text:
                return text
            logger.warning(f"[synthesis] Intento {intento + 1}: contenido vacío, reintentando")
        except Exception as e:
            logger.warning(f"[synthesis] Intento {intento + 1} falló ({e})")
            continue

    logger.warning("[synthesis] No se pudo generar la síntesis tras los reintentos")
    return None


def _store_synthesis(user_id: str, synthesis_text: str):
    """Guarda (o actualiza) la síntesis del usuario y marca la fecha de consolidación."""
    db_run(
        "INSERT INTO user_synthesis (user_id, synthesis_text, last_consolidated_at, updated_at) "
        "VALUES (%s, %s, NOW(), NOW()) "
        "ON CONFLICT (user_id) DO UPDATE SET "
        "synthesis_text = EXCLUDED.synthesis_text, "
        "last_consolidated_at = NOW(), updated_at = NOW()",
        (user_id, synthesis_text)
    )


def regenerate_synthesis(user_id: str) -> bool:
    """
    Regenera la síntesis viva de un usuario a partir de su material reciente.
    Pensada para llamarse desde el endpoint de consolidación (asíncrono).
    Devuelve True si se actualizó, False si no había material o falló el LLM.
    """
    row = db_one(
        "SELECT last_consolidated_at FROM user_synthesis WHERE user_id = %s",
        (user_id,)
    )
    since_iso = None
    if row and row.get("last_consolidated_at"):
        since_iso = row["last_consolidated_at"].isoformat() \
            if hasattr(row["last_consolidated_at"], "isoformat") else str(row["last_consolidated_at"])

    material = _gather_recent_material(user_id, since_iso)
    existing = get_synthesis(user_id)

    # Si no hay nada nuevo que integrar y ya existe síntesis, no gastamos una
    # llamada al LLM — la síntesis previa sigue vigente.
    if not material:
        if existing:
            logger.info(f"[synthesis] user={user_id}: sin material nuevo, se mantiene la síntesis actual")
            # Actualizamos la marca temporal para no reprocesarlo en cada pasada.
            db_run(
                "UPDATE user_synthesis SET last_consolidated_at = NOW() WHERE user_id = %s",
                (user_id,)
            )
            return False
        logger.info(f"[synthesis] user={user_id}: sin material y sin síntesis previa, nada que hacer")
        return False

    profile_facts = _get_profile_facts(user_id)
    prompt = _build_synthesis_prompt(existing, profile_facts, material)
    new_synthesis = _call_llm_for_synthesis(prompt)

    if not new_synthesis:
        logger.warning(f"[synthesis] user={user_id}: el LLM no devolvió síntesis")
        return False

    _store_synthesis(user_id, new_synthesis)
    logger.info(f"[synthesis] user={user_id}: síntesis actualizada ({len(new_synthesis)} caracteres)")
    return True


def get_users_needing_consolidation(limit: int = 3) -> list[str]:
    """
    Devuelve los IDs de usuarios con actividad nueva desde su última
    consolidación (o que nunca han sido consolidados). Limitado por 'limit'
    para que cada pasada del endpoint sea acotada y no exceda tiempos de
    ejecución ni el TPM de Groq. Con más usuarios, se irán procesando en
    pasadas sucesivas.
    """
    rows = db_all(
        """
        SELECT DISTINCT m.user_id
        FROM messages m
        LEFT JOIN user_synthesis s ON s.user_id = m.user_id
        WHERE m.user_id IS NOT NULL
          AND (s.last_consolidated_at IS NULL OR m.created_at > s.last_consolidated_at)
        LIMIT %s
        """,
        (limit,)
    )
    return [str(r["user_id"]) for r in (rows or [])]


def run_consolidation(limit: int = 3) -> dict:
    """
    Ejecuta una pasada de consolidación: regenera la síntesis de hasta 'limit'
    usuarios con actividad nueva. Devuelve un resumen del trabajo hecho.
    """
    user_ids = get_users_needing_consolidation(limit)
    actualizados = 0
    for uid in user_ids:
        try:
            if regenerate_synthesis(uid):
                actualizados += 1
        except Exception as e:
            logger.warning(f"[synthesis] Error consolidando user={uid}: {e}")

    resumen = {
        "usuarios_revisados": len(user_ids),
        "sintesis_actualizadas": actualizados,
    }
    logger.info(f"[synthesis] Consolidación: {resumen}")
    return resumen
