"""
astrology.py

Capa de orquestación entre astro_core (motor de cálculo puro) y GaIA:
  - Convierte hora local -> UTC usando el timezone resuelto por geocoding.
  - Guarda / lee la carta natal del usuario en Supabase.
  - Calcula tránsitos actuales sobre la carta guardada.
  - Formatea todo como bloque de texto para inyectar en el system prompt de Groq.
"""

import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from db import db_one, db_run
from geocoding import geocode_location
from astro_core.birth_chart import calculate_birth_chart
from astro_core.transits import get_current_transits_report
from astro_core.models import BirthChart, PlanetPosition, HouseCusp, Aspect

logger = logging.getLogger(__name__)


# ── Crear / consultar carta natal ──────────────────────────────────────────

def create_birth_chart_for_user(user_id: str, birth_date: str, birth_time: str,
                                 birth_place: str) -> dict | None:
    """
    Calcula y guarda la carta natal de un usuario.

    Args:
        user_id: UUID del usuario (de la tabla users/sessions existente)
        birth_date: 'YYYY-MM-DD'
        birth_time: 'HH:MM' en hora LOCAL del lugar de nacimiento
        birth_place: nombre libre, ej. "Alicante, España"

    Returns:
        dict con la carta calculada (formato BirthChart.to_dict()) o None si falla.
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

    # Upsert: un usuario tiene una única carta natal activa
    db_run(
        """
        INSERT INTO user_birth_charts
            (user_id, birth_datetime_utc, birth_lat, birth_lon, birth_place,
             sun_sign, moon_sign, rising_sign, full_chart)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (user_id) DO UPDATE SET
            birth_datetime_utc = EXCLUDED.birth_datetime_utc,
            birth_lat           = EXCLUDED.birth_lat,
            birth_lon           = EXCLUDED.birth_lon,
            birth_place          = EXCLUDED.birth_place,
            sun_sign             = EXCLUDED.sun_sign,
            moon_sign            = EXCLUDED.moon_sign,
            rising_sign          = EXCLUDED.rising_sign,
            full_chart           = EXCLUDED.full_chart,
            updated_at           = NOW()
        """,
        (
            user_id, utc_dt, geo["latitude"], geo["longitude"], geo["display_name"],
            sun.sign if sun else None,
            moon.sign if moon else None,
            chart_dict["ascendant"]["sign"] if chart_dict["ascendant"] else None,
            json.dumps(chart_dict),
        )
    )

    logger.info(f"[astrology] Carta natal guardada para user={user_id}: "
                f"Sol {sun.sign if sun else '?'}, Asc "
                f"{chart_dict['ascendant']['sign'] if chart_dict['ascendant'] else '?'}")
    return chart_dict


def get_birth_chart_for_user(user_id: str) -> dict | None:
    """Recupera la carta natal guardada de un usuario (como dict), o None si no tiene."""
    row = db_one(
        "SELECT full_chart, birth_lat, birth_lon FROM user_birth_charts WHERE user_id = %s",
        (user_id,)
    )
    if not row:
        return None
    chart_dict = row["full_chart"]
    # psycopg2 con RealDictCursor puede devolver JSONB ya parseado o como string
    if isinstance(chart_dict, str):
        chart_dict = json.loads(chart_dict)
    return chart_dict


def _dict_to_birth_chart(chart_dict: dict) -> BirthChart:
    """Reconstruye un objeto BirthChart a partir del dict guardado en DB,
    necesario para reutilizar get_current_transits_report() de astro_core."""
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

def get_transits_for_user(user_id: str) -> dict | None:
    """Calcula los tránsitos actuales sobre la carta natal guardada del usuario."""
    chart_dict = get_birth_chart_for_user(user_id)
    if not chart_dict:
        return None
    chart = _dict_to_birth_chart(chart_dict)
    return get_current_transits_report(chart)


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
}


def _planet_es(name: str) -> str:
    return _PLANET_ES.get(name, name.capitalize())


def format_astrology_context(user_id: str) -> str:
    """
    Construye el bloque de texto astrológico para inyectar en el system prompt.
    Devuelve '' si el usuario no tiene carta natal guardada (GaIA simplemente
    no menciona astrología, no hace falta que sepa por qué).
    """
    chart_dict = get_birth_chart_for_user(user_id)
    if not chart_dict:
        return ""

    transits = get_transits_for_user(user_id)

    lines = ["## CARTA NATAL DEL USUARIO (datos reales, calculados con precisión astronómica)"]
    sun_sign = chart_dict["planets"][0]["sign"] if chart_dict["planets"] else None

    for p in chart_dict["planets"]:
        house_suffix = f", casa {p['house']}" if p.get("house") else ""
        lines.append(f"- {_planet_es(p['name'])}: {p['sign']} {p['degree_in_sign']}°{house_suffix}")

    if chart_dict.get("ascendant"):
        lines.append(f"- Ascendente: {chart_dict['ascendant']['sign']} "
                     f"{chart_dict['ascendant']['degree']}°")

    if transits and transits.get("most_significant"):
        lines.append("\n## TRÁNSITOS ACTIVOS AHORA MISMO (aspectos más exactos, ordenados por relevancia)")
        for t in transits["most_significant"]:
            tone = _ASPECT_TONE.get(t["aspect_type"], "")
            lines.append(
                f"- {_planet_es(t['transit_planet'])} en tránsito ({t['transit_sign']}) "
                f"en {t['aspect_type']} con tu {_planet_es(t['natal_planet'])} natal "
                f"({t['natal_sign']}, casa {t['natal_house']}) — orbe {t['orb']}°. {tone}."
            )

    lines.append(
        "\nUsa estos datos SOLO si el usuario pregunta por su carta astral, su signo, "
        "sus tránsitos, o algo que conecte naturalmente con esto. No lo menciones de forma "
        "forzada en temas que no lo pidan. Cuando lo uses, interpreta con UNA hipótesis "
        "fuerte, no un catálogo de posibilidades — igual que haces con el resto de tu saber."
    )

    return "\n".join(lines)
