"""
transits.py
Cálculo de tránsitos: compara las posiciones planetarias en un instante dado
(por defecto, ahora mismo) contra la carta natal del usuario.
"""

from datetime import datetime, timezone
from astro_core.birth_chart import calculate_planet_positions, MAJOR_ASPECTS
from astro_core.models import BirthChart, PlanetPosition, Aspect
from astro_core.relevancia import nivel_transito, puntuar_transito
from astro_core.angulos import puntos_angulares, aspectos_a_angulos


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


def _casa_de_transito(natal_chart: BirthChart, longitud: float):
    """Casa natal (casas enteras) por la que transita un punto, o None si no hay Ascendente fiable."""
    if natal_chart.ascendant is None or not natal_chart.time_known:
        return None
    return ((int((longitud % 360) // 30) - int(natal_chart.ascendant // 30)) % 12) + 1


def calculate_transit_aspects(natal_chart: BirthChart, transit_planets: list[PlanetPosition] = None,
                              include_angles: bool = False):
    """
    Compara los planetas en tránsito contra los planetas natales, devolviendo
    los aspectos activos entre ambos conjuntos.

    Esto es distinto de calculate_aspects() en birth_chart.py, que compara
    planetas natales entre sí. Aquí comparamos tránsito -> natal.

    include_angles añade los aspectos a Ascendente, Descendente, Medio Cielo y Fondo del
    Cielo (solo si se conoce la hora de nacimiento). Va desactivado por defecto porque el
    dibujo SVG de tránsitos solo sabe pintar planetas.
    """
    if transit_planets is None:
        transit_planets = get_planet_positions_at(None, natal_chart.latitude, natal_chart.longitude)

    puntos = []
    if include_angles and natal_chart.time_known and natal_chart.ascendant is not None \
            and natal_chart.midheaven is not None:
        puntos = puntos_angulares(natal_chart.ascendant, natal_chart.midheaven)

    transit_aspects = []
    for t_planet in transit_planets:
        t_house = _casa_de_transito(natal_chart, t_planet.longitude)
        for punto_aspecto in aspectos_a_angulos(t_planet.longitude, puntos, factor_orbe=0.6):
            punto = punto_aspecto["point"]
            transit_aspects.append({
                "transit_planet": t_planet.name,
                "transit_sign": t_planet.sign,
                "transit_house": t_house,
                "transit_retrograde": t_planet.is_retrograde,
                "natal_planet": punto["name"],
                "natal_sign": punto["sign"],
                "natal_house": punto["house"],
                "aspect_type": punto_aspecto["aspect_type"],
                "orb": punto_aspecto["orb"],
                "exact_angle": punto_aspecto["exact_angle"],
                "nivel": nivel_transito(t_planet.name),
                "relevancia": puntuar_transito(t_planet.name, punto["name"],
                                               punto_aspecto["aspect_type"],
                                               punto_aspecto["orb"], punto_aspecto["orb_max"]),
            })
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
                        "transit_house": t_house,
                        "transit_retrograde": t_planet.is_retrograde,
                        "natal_planet": n_planet.name,
                        "natal_sign": n_planet.sign,
                        "natal_house": n_planet.house,
                        "aspect_type": aspect_name,
                        "orb": round(orb, 2),
                        "exact_angle": exact_angle,
                        "nivel": nivel_transito(t_planet.name),
                        "relevancia": puntuar_transito(t_planet.name, n_planet.name,
                                                       aspect_name, orb, transit_orb),
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
    aspects = calculate_transit_aspects(natal_chart, transit_planets, include_angles=True)

    # Ordenar por relevancia (planeta lento sobre punto personal y cerca de exacto
    # primero); a igualdad, el más exacto. Ordenar solo por orbe ponía una Luna de
    # paso por delante de Plutón.
    aspects.sort(key=lambda a: (-a["relevancia"], a["orb"]))

    effective_dt = target_datetime if target_datetime else datetime.now(timezone.utc)

    return {
        "calculated_at": effective_dt.isoformat(),
        "transit_positions": [p.to_dict() for p in transit_planets],
        "active_aspects": aspects,
        "most_significant": aspects[:8] if aspects else [],
    }


# Alias retrocompatible — el código existente (astrology.py) sigue llamando a este nombre
def get_current_transits_report(natal_chart: BirthChart) -> dict:
    return get_transits_report_for_date(natal_chart, None)

