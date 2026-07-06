"""
astrology.py

Capa de orquestación entre astro_core (motor de cálculo puro) y GaIA:
  - Convierte hora local -> UTC usando el timezone resuelto por geocoding.
  - Guarda / lee cartas natales en Supabase — UN USUARIO PUEDE TENER VARIAS
    CARTAS (la suya propia, pareja, hijos, familiares...), cada una
    identificada por un "person_label" (ej. "yo", "Marco (hijo)").
  - Calcula tránsitos sobre cualquiera de esas cartas.
  - Formatea todo como bloque de texto para inyectar en el system prompt de Groq.

Retrocompatibilidad: todas las funciones que antes tomaban solo user_id
ahora aceptan un person_label opcional que por defecto vale "yo" — así el
flujo principal de chat (que siempre habla de "la carta del usuario") no
necesita cambiar ninguna llamada existente.
"""

import json
import logging
import re
from datetime import datetime, timezone, date
from zoneinfo import ZoneInfo

from db import db_one, db_all, db_run
from geocoding import geocode_location
from astro_core.birth_chart import calculate_birth_chart
from astro_core.transits import get_current_transits_report, get_transits_report_for_date, get_planet_positions_at, calculate_transit_aspects
from astro_core.models import BirthChart, PlanetPosition, HouseCusp, Aspect
from astro_core.chart_svg import render_birth_chart_svg, render_transit_chart_svg

logger = logging.getLogger(__name__)

DEFAULT_PERSON_LABEL = "yo"


# ── Crear / consultar cartas natales (multi-persona) ────────────────────

def create_birth_chart_for_user(user_id: str, birth_date: str, birth_time: str,
                                 birth_place: str, person_label: str = DEFAULT_PERSON_LABEL,
                                 relationship: str = None) -> dict | None:
    """
    Calcula y guarda una carta natal para el usuario. Un usuario puede tener
    varias cartas guardadas (la suya propia y la de otras personas), cada
    una distinguida por person_label.
    """
    geo = geocode_location(birth_place)
    if not geo:
        logger.error(f"[astrology] No se pudo geocodificar '{birth_place}'")
        return None

    try:
        naive_dt = datetime.strptime(f"{birth_date} {birth_time}", "%Y-%m-%d %H:%M")
        local_dt = naive_dt.replace(tzinfo=ZoneInfo(geo["timezone"]))
        utc_dt = local_dt.astimezone(ZoneInfo("UTC"))
    except ValueError as e:
        logger.error(f"[astrology] Fecha/hora inválida: {e}")
        return None

    chart = calculate_birth_chart(
        birth_datetime=utc_dt,
        latitude=geo["latitude"],
        longitude=geo["longitude"],
        location_name=geo["display_name"],
    )
    chart_dict = chart.to_dict()

    sun = chart.get_planet("sun")
    moon = chart.get_planet("moon")

    db_run(
        """
        INSERT INTO user_birth_charts
            (user_id, person_label, relationship, birth_datetime_utc, birth_lat, birth_lon,
             birth_place, sun_sign, moon_sign, rising_sign, full_chart)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (user_id, person_label) DO UPDATE SET
            relationship        = EXCLUDED.relationship,
            birth_datetime_utc  = EXCLUDED.birth_datetime_utc,
            birth_lat           = EXCLUDED.birth_lat,
            birth_lon           = EXCLUDED.birth_lon,
            birth_place         = EXCLUDED.birth_place,
            sun_sign            = EXCLUDED.sun_sign,
            moon_sign            = EXCLUDED.moon_sign,
            rising_sign         = EXCLUDED.rising_sign,
            full_chart          = EXCLUDED.full_chart,
            updated_at          = NOW()
        """,
        (
            user_id, person_label, relationship, utc_dt, geo["latitude"], geo["longitude"], geo["display_name"],
            sun.sign if sun else None,
            moon.sign if moon else None,
            chart_dict["ascendant"]["sign"] if chart_dict["ascendant"] else None,
            json.dumps(chart_dict),
        )
    )

    logger.info(f"[astrology] Carta natal guardada para user={user_id} person='{person_label}': "
                f"Sol {sun.sign if sun else '?'}, Asc "
                f"{chart_dict['ascendant']['sign'] if chart_dict['ascendant'] else '?'}")
    return chart_dict


def get_birth_chart_for_user(user_id: str, person_label: str = DEFAULT_PERSON_LABEL) -> dict | None:
    """Recupera una carta natal guardada (como dict), o None si no existe esa persona."""
    row = db_one(
        "SELECT full_chart, birth_lat, birth_lon FROM user_birth_charts WHERE user_id = %s AND person_label = %s",
        (user_id, person_label)
    )
    if not row:
        return None
    chart_dict = row["full_chart"]
    if isinstance(chart_dict, str):
        chart_dict = json.loads(chart_dict)
    return chart_dict


def list_charts_for_user(user_id: str) -> list:
    """Lista todas las personas con carta natal guardada bajo este usuario."""
    rows = db_all(
        """
        SELECT person_label, relationship, sun_sign, moon_sign, rising_sign, birth_place, created_at
        FROM user_birth_charts WHERE user_id = %s ORDER BY created_at ASC
        """,
        (user_id,)
    )
    return rows or []


def delete_birth_chart_for_user(user_id: str, person_label: str) -> bool:
    """Elimina la carta natal de una persona concreta bajo este usuario."""
    db_run(
        "DELETE FROM user_birth_charts WHERE user_id = %s AND person_label = %s",
        (user_id, person_label)
    )
    return True


def _dict_to_birth_chart(chart_dict: dict) -> BirthChart:
    """Reconstruye un objeto BirthChart a partir del dict guardado en DB."""
    planets = [
        PlanetPosition(
            name=p["name"], longitude=p["longitude"], sign=p["sign"],
            degree_in_sign=p["degree_in_sign"], is_retrograde=p.get("is_retrograde", False),
            house=p.get("house"),
        ) for p in chart_dict["planets"]
    ]
    return BirthChart(
        birth_datetime=datetime.fromisoformat(chart_dict["birth_datetime"]),
        latitude=chart_dict["latitude"],
        longitude=chart_dict["longitude"],
        location_name=chart_dict.get("location_name"),
        planets=planets,
        ascendant=chart_dict["ascendant"]["absolute_longitude"] if chart_dict.get("ascendant") else None,
        midheaven=chart_dict["midheaven"]["absolute_longitude"] if chart_dict.get("midheaven") else None,
    )


# ── Tránsitos ────────────────────────────────────────────────────────────

def get_transits_for_user(user_id: str, person_label: str = DEFAULT_PERSON_LABEL) -> dict | None:
    """Calcula los tránsitos actuales sobre la carta natal guardada de esa persona."""
    chart_dict = get_birth_chart_for_user(user_id, person_label)
    if not chart_dict:
        return None
    chart = _dict_to_birth_chart(chart_dict)
    return get_current_transits_report(chart)


def get_transits_for_user_on_date(user_id: str, target_date: str,
                                   person_label: str = DEFAULT_PERSON_LABEL) -> dict | None:
    """Calcula los tránsitos sobre la carta natal guardada para una fecha determinada."""
    chart_dict = get_birth_chart_for_user(user_id, person_label)
    if not chart_dict:
        return None
    try:
        target_dt = datetime.strptime(target_date, "%Y-%m-%d").replace(hour=12, tzinfo=timezone.utc)
    except ValueError:
        logger.error(f"[astrology] Fecha inválida para tránsitos: '{target_date}'")
        return None
    chart = _dict_to_birth_chart(chart_dict)
    return get_transits_report_for_date(chart, target_dt)


# ── Generación de gráficos SVG ──────────────────────────────────────────

def get_birth_chart_svg_for_user(user_id: str, person_label: str = DEFAULT_PERSON_LABEL) -> str | None:
    """Genera el SVG de una carta natal guardada, o None si no existe."""
    chart_dict = get_birth_chart_for_user(user_id, person_label)
    if not chart_dict:
        return None
    chart = _dict_to_birth_chart(chart_dict)
    return render_birth_chart_svg(chart)


def get_transit_chart_svg_for_user(user_id: str, target_date: str = None,
                                    person_label: str = DEFAULT_PERSON_LABEL) -> str | None:
    """Genera el SVG de la carta bi-wheel (natal + tránsitos) para una fecha determinada."""
    chart_dict = get_birth_chart_for_user(user_id, person_label)
    if not chart_dict:
        return None

    target_dt = None
    if target_date:
        try:
            target_dt = datetime.strptime(target_date, "%Y-%m-%d").replace(hour=12, tzinfo=timezone.utc)
        except ValueError:
            logger.error(f"[astrology] Fecha inválida para SVG de tránsitos: '{target_date}'")
            return None

    chart = _dict_to_birth_chart(chart_dict)
    transit_planets = get_planet_positions_at(target_dt, chart.latitude, chart.longitude)
    transit_aspects = calculate_transit_aspects(chart, transit_planets)
    return render_transit_chart_svg(chart, transit_planets, transit_aspects)


# ── Formateo para el prompt de Groq ─────────────────────────────────────

_ASPECT_TONE = {
    "conjunción": "una fusión intensa, foco concentrado",
    "oposición": "una tensión que pide equilibrio entre dos polos",
    "cuadratura": "una fricción que empuja a la acción o al ajuste",
    "trígono": "un flujo fácil, apoyo natural",
    "sextil": "una oportunidad que requiere un pequeño esfuerzo para activarse",
}

_PLANET_ES = {
    "sun": "Sol", "moon": "Luna", "mercury": "Mercurio", "venus": "Venus",
    "mars": "Marte", "jupiter": "Júpiter", "saturn": "Saturno",
    "uranus": "Urano", "neptune": "Neptuno", "pluto": "Plutón",
    "north_node": "Nodo Norte", "south_node": "Nodo Sur",
    "lilith": "Lilith", "chiron": "Quirón",
}


def _planet_es(name: str) -> str:
    return _PLANET_ES.get(name, name.capitalize())


def format_astrology_context(user_id: str, target_date: str = None,
                              person_label: str = DEFAULT_PERSON_LABEL) -> str:
    """
    Construye el bloque de texto astrológico para inyectar en el system prompt.
    Devuelve '' si no existe carta guardada para esa persona.
    """
    chart_dict = get_birth_chart_for_user(user_id, person_label)
    if not chart_dict:
        return ""

    if target_date:
        transits = get_transits_for_user_on_date(user_id, target_date, person_label)
    else:
        transits = get_transits_for_user(user_id, person_label)

    is_self = person_label == DEFAULT_PERSON_LABEL
    subject = "DEL USUARIO" if is_self else f"DE {person_label.upper()}"
    possessive = "tu" if is_self else f"su ({person_label})"

    lines = [f"## CARTA NATAL {subject} (datos reales, calculados con precisión astronómica)"]

    for p in chart_dict["planets"]:
        house_suffix = f", casa {p['house']}" if p.get("house") else ""
        lines.append(f"- {_planet_es(p['name'])}: {p['sign']} {p['degree_in_sign']}°{house_suffix}")

    if chart_dict.get("ascendant"):
        lines.append(f"- Ascendente: {chart_dict['ascendant']['sign']} "
                     f"{chart_dict['ascendant']['degree']}°")

    if transits and transits.get("most_significant"):
        header = (f"\n## TRÁNSITOS PARA EL {target_date} (aspectos más exactos, ordenados por relevancia)"
                  if target_date else
                  "\n## TRÁNSITOS ACTIVOS AHORA MISMO (aspectos más exactos, ordenados por relevancia)")
        lines.append(header)
        for t in transits["most_significant"]:
            tone = _ASPECT_TONE.get(t["aspect_type"], "")
            lines.append(
                f"- {_planet_es(t['transit_planet'])} en tránsito ({t['transit_sign']}) "
                f"en {t['aspect_type']} con {possessive} {_planet_es(t['natal_planet'])} natal "
                f"({t['natal_sign']}, casa {t['natal_house']}) — orbe {t['orb']}°. {tone}."
            )

    lines.append(
        "\nUsa estos datos SOLO si el usuario pregunta por esta carta astral, este signo, "
        "estos tránsitos, o algo que conecte naturalmente con esto. No lo menciones de forma "
        "forzada en temas que no lo pidan. Cuando lo uses, interpreta con UNA hipótesis "
        "fuerte, no un catálogo de posibilidades — igual que haces con el resto de tu saber."
    )

    return "\n".join(lines)


# ── Detección de fecha y de persona en lenguaje natural ─────────────────

def extract_date_from_message(message: str) -> str | None:
    """
    Detecta si el mensaje del usuario menciona una fecha concreta para la que
    quiere conocer sus tránsitos. Usa el modelo pequeño de Groq solo para esta
    extracción, con un filtro rápido previo para no gastar tokens de más.
    """
    trigger_words = ["tránsito", "transito", "carta", "signo", "astral", "horóscopo",
                      "horoscopo", "planeta", "luna", "sol en", "ascendente"]
    date_hint_words = ["el día", "el dia", "para el", "cuando", "cuándo", "próximo",
                       "proximo", "próxima", "proxima", "mañana", "hoy", "cumpleaños",
                       "cumpleanos", "de enero", "de febrero", "de marzo", "de abril",
                       "de mayo", "de junio", "de julio", "de agosto", "de septiembre",
                       "de octubre", "de noviembre", "de diciembre"]
    msg_lower = message.lower()
    if not any(w in msg_lower for w in trigger_words):
        return None
    if not any(w in msg_lower for w in date_hint_words):
        return None

    from llm import _client, GROQ_MODEL_FALLBACK
    if not _client:
        return None

    today_str = date.today().isoformat()
    try:
        response = _client.chat.completions.create(
            model=GROQ_MODEL_FALLBACK,
            messages=[{
                "role": "system",
                "content": (
                    f"Hoy es {today_str}. El usuario puede mencionar una fecha para la "
                    "que quiere conocer sus tránsitos astrológicos. Si el mensaje menciona "
                    "una fecha concreta (incluyendo relativas como 'mañana', 'el próximo "
                    "lunes', 'mi cumpleaños el 3 de marzo'), responde ÚNICAMENTE con esa "
                    "fecha en formato YYYY-MM-DD, sin texto adicional. Si NO menciona "
                    "ninguna fecha, responde ÚNICAMENTE con la palabra NONE."
                )
            }, {
                "role": "user",
                "content": message
            }],
            temperature=0,
            max_tokens=20,
        )
        result = response.choices[0].message.content.strip()
        if re.match(r"^\d{4}-\d{2}-\d{2}$", result):
            return result
        return None
    except Exception as e:
        logger.warning(f"[astrology] No se pudo extraer fecha del mensaje: {e}")
        return None


def extract_person_from_message(user_id: str, message: str) -> str:
    """
    Detecta si el mensaje se refiere a la carta de OTRA persona guardada
    (ej. "la carta de mi hijo Marco") en vez de la propia del usuario.
    Compara contra las personas realmente guardadas, para no alucinar
    etiquetas inexistentes.

    Returns:
        El person_label correspondiente si se detecta una mención clara,
        o DEFAULT_PERSON_LABEL ("yo") si no hay coincidencia.
    """
    charts = list_charts_for_user(user_id)
    other_people = [c["person_label"] for c in charts if c["person_label"] != DEFAULT_PERSON_LABEL]
    if not other_people:
        return DEFAULT_PERSON_LABEL

    msg_lower = message.lower()
    for label in other_people:
        name_part = label.split('(')[0].strip().lower()
        if name_part and name_part in msg_lower:
            return label

    return DEFAULT_PERSON_LABEL


def extract_new_chart_request(message: str) -> dict | None:
    """
    Detecta si el mensaje pide calcular la carta natal de OTRA persona con
    todos los datos necesarios incluidos en el propio texto (ej. "calcula la
    carta de mi hija Elena, nació el 3 de marzo de 2015 a las 09:20 en
    Valencia"). Usa el modelo pequeño de Groq para extraer los campos
    estructurados solo si el mensaje realmente parece una petición de este
    tipo (filtro rápido previo).

    Returns:
        dict con person_label, relationship, birth_date, birth_time,
        birth_place — o None si no se detecta una petición completa y clara.
        Si falta algún dato imprescindible, también devuelve None (es más
        seguro pedir el dato por el modal que adivinarlo).
    """
    trigger_words = ["calcula la carta", "calcula su carta", "haz la carta",
                     "carta de mi", "carta natal de", "quiero la carta de"]
    msg_lower = message.lower()
    if not any(w in msg_lower for w in trigger_words):
        return None

    from llm import _client, GROQ_MODEL_FALLBACK
    if not _client:
        return None

    try:
        response = _client.chat.completions.create(
            model=GROQ_MODEL_FALLBACK,
            messages=[{
                "role": "system",
                "content": (
                    "Extraes datos para calcular una carta natal de OTRA persona (no quien "
                    "escribe) a partir de un mensaje en español. Necesitas: nombre de la "
                    "persona, su relación con quien escribe (hijo/a, pareja, madre, padre, "
                    "amigo/a, etc — o null si no se menciona), fecha de nacimiento "
                    "(YYYY-MM-DD), hora de nacimiento (HH:MM, 24h — o null si no se menciona), "
                    "y lugar de nacimiento. "
                    "Responde ÚNICAMENTE con un JSON con estas claves exactas: "
                    "person_name, relationship, birth_date, birth_time, birth_place. "
                    "Si falta el nombre, la fecha, o el lugar (los tres son imprescindibles), "
                    "responde ÚNICAMENTE con la palabra NONE. Si falta solo la hora, usa "
                    "\"12:00\" como valor por defecto. No expliques nada, solo el JSON o NONE."
                )
            }, {
                "role": "user",
                "content": message
            }],
            temperature=0,
            max_tokens=150,
        )
        result = response.choices[0].message.content.strip()
        if result.upper() == "NONE":
            return None

        # Limpia posibles fences de markdown que el modelo pueda añadir
        result = re.sub(r'^```json\s*|\s*```$', '', result.strip())
        parsed = json.loads(result)

        if not all([parsed.get("person_name"), parsed.get("birth_date"), parsed.get("birth_place")]):
            return None

        relationship = parsed.get("relationship")
        person_label = f"{parsed['person_name']} ({relationship})" if relationship else parsed["person_name"]

        return {
            "person_label": person_label,
            "relationship": relationship,
            "birth_date": parsed["birth_date"],
            "birth_time": parsed.get("birth_time") or "12:00",
            "birth_place": parsed["birth_place"],
        }
    except Exception as e:
        logger.warning(f"[astrology] No se pudo extraer petición de nueva carta: {e}")
        return None
