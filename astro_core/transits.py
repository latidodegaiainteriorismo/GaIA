"""
transits.py
Cálculo de tránsitos: compara las posiciones planetarias en un instante dado
(por defecto, ahora mismo) contra la carta natal del usuario.
"""

from datetime import datetime, timezone
from astro_core.birth_chart import calculate_planet_positions, MAJOR_ASPECTS
from astro_core.models import BirthChart, PlanetPosition, Aspect


def get_planet_positions_at(target_datetime: datetime = None, latitude: float = 0.0, longitude: float = 0.0,
                             include_extra_bodies: bool = True):
    """
    Devuelve las posiciones planetarias en un instante dado (geocéntricas,
    prácticamente independientes de la ubicación del observador para tránsitos).
    Incluye por defecto Nodo Norte/Sur, Lilith media y Quirón, igual que la
    carta natal, para que los tránsitos de estos puntos también sean detectables.

    Args:
        target_datetime: instante a calcular. Si es None, usa el momento actual (UTC).
                          Si se pasa naive (sin tzinfo), se asume UTC.
    """
    if target_datetime is None:
        target_datetime = datetime.now(timezone.utc)
    elif target_datetime.tzinfo is None:
        target_datetime = target_datetime.replace(tzinfo=timezone.utc)

    return calculate_planet_positions(target_datetime, latitude, longitude, include_extra_bodies)


# Alias retrocompatible — código existente que llame a esta función sigue funcionando igual
def get_current_planet_positions(latitude: float = 0.0, longitude: float = 0.0):
    return get_planet_positions_at(None, latitude, longitude)


def calculate_transit_aspects(natal_chart: BirthChart, transit_planets: list[PlanetPosition] = None):
    """
    Compara los planetas en tránsito contra los planetas natales, devolviendo
    los aspectos activos entre ambos conjuntos.

    Esto es distinto de calculate_aspects() en birth_chart.py, que compara
    planetas natales entre sí. Aquí comparamos tránsito -> natal.
    """
    if transit_planets is None:
        transit_planets = get_planet_positions_at(None, natal_chart.latitude, natal_chart.longitude)

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


def get_transits_report_for_date(natal_chart: BirthChart, target_datetime: datetime = None) -> dict:
    """
    Genera un reporte completo de tránsitos para una fecha dada (o el momento
    actual si no se especifica), para usar como contexto en interpretaciones LLM.

    Args:
        natal_chart: carta natal ya calculada del usuario
        target_datetime: fecha/hora UTC a analizar. None = ahora mismo.
    """
    transit_planets = get_planet_positions_at(target_datetime, natal_chart.latitude, natal_chart.longitude)
    aspects = calculate_transit_aspects(natal_chart, transit_planets)

    # Ordenar por orbe (más exactos primero = más relevantes)
    aspects.sort(key=lambda a: a["orb"])

    effective_dt = target_datetime if target_datetime else datetime.now(timezone.utc)

    return {
        "calculated_at": effective_dt.isoformat(),
        "transit_positions": [p.to_dict() for p in transit_planets],
        "active_aspects": aspects,
        "most_significant": aspects[:5] if aspects else [],
    }


# Alias retrocompatible — el código existente (astrology.py) sigue llamando a este nombre
def get_current_transits_report(natal_chart: BirthChart) -> dict:
    return get_transits_report_for_date(natal_chart, None)

