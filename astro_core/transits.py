"""
transits.py
Cálculo de tránsitos: compara las posiciones planetarias ACTUALES
contra la carta natal del usuario, para detectar aspectos activos.
"""

from datetime import datetime, timezone
from astro_core.birth_chart import calculate_planet_positions, MAJOR_ASPECTS
from astro_core.models import BirthChart, PlanetPosition, Aspect


def get_current_planet_positions(latitude: float = 0.0, longitude: float = 0.0):
    """
    Devuelve las posiciones planetarias actuales (geocéntricas, prácticamente
    independientes de la ubicación del observador para fines de tránsitos).
    """
    now = datetime.now(timezone.utc)
    return calculate_planet_positions(now, latitude, longitude)


def calculate_transit_aspects(natal_chart: BirthChart, transit_planets: list[PlanetPosition] = None):
    """
    Compara los planetas en tránsito (posición actual) contra los planetas
    natales, devolviendo los aspectos activos entre ambos conjuntos.

    Esto es distinto de calculate_aspects() en birth_chart.py, que compara
    planetas natales entre sí. Aquí comparamos tránsito -> natal.
    """
    if transit_planets is None:
        transit_planets = get_current_planet_positions(natal_chart.latitude, natal_chart.longitude)

    transit_aspects = []
    for t_planet in transit_planets:
        for n_planet in natal_chart.planets:
            diff = abs(t_planet.longitude - n_planet.longitude)
            if diff > 180:
                diff = 360 - diff

            for aspect_name, exact_angle, max_orb in MAJOR_ASPECTS:
                # Orbes más estrechos para tránsitos (son más precisos/puntuales que la natal)
                transit_orb = max_orb * 0.6
                orb = abs(diff - exact_angle)
                if orb <= transit_orb:
                    transit_aspects.append({
                        "transit_planet": t_planet.name,
                        "transit_sign": t_planet.sign,
                        "natal_planet": n_planet.name,
                        "natal_sign": n_planet.sign,
                        "natal_house": n_planet.house,
                        "aspect_type": aspect_name,
                        "orb": round(orb, 2),
                        "exact_angle": exact_angle,
                    })
                    break
    return transit_aspects


def get_current_transits_report(natal_chart: BirthChart) -> dict:
    """
    Genera un reporte completo de tránsitos actuales para usar como
    contexto en la generación de interpretaciones (ej. para pasar a Groq/LLM).
    """
    transit_planets = get_current_planet_positions(natal_chart.latitude, natal_chart.longitude)
    aspects = calculate_transit_aspects(natal_chart, transit_planets)

    # Ordenar por orbe (más exactos primero = más relevantes)
    aspects.sort(key=lambda a: a["orb"])

    return {
        "calculated_at": datetime.now(timezone.utc).isoformat(),
        "transit_positions": [p.to_dict() for p in transit_planets],
        "active_aspects": aspects,
        "most_significant": aspects[:5] if aspects else [],
    }
