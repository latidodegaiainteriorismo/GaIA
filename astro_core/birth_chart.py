"""
birth_chart.py
Cálculo de la carta natal completa: posiciones planetarias, Ascendente,
Medio Cielo, casas (sistema Placidus simplificado / Whole Sign) y aspectos.
"""

import math
from datetime import datetime, timezone
from skyfield.api import wgs84
from astro_core.ephemeris import get_timescale, get_ephemeris, PLANET_KEYS
from astro_core.models import BirthChart, PlanetPosition, HouseCusp, Aspect, longitude_to_sign
from astro_core.extra_bodies import calculate_extra_bodies


# Definición de aspectos mayores: (nombre, ángulo exacto, orbe permitido en grados)
MAJOR_ASPECTS = [
    ("conjunción", 0, 10),
    ("sextil", 60, 8),
    ("cuadratura", 90, 10),
    ("trígono", 120, 8),
    ("oposición", 180, 10),
]


def _ecliptic_longitude(eph, body_key, t, observer=None):
    """
    Calcula la longitud eclíptica geocéntrica (o topocéntrica si se da observer)
    de un cuerpo celeste en un instante t.
    """
    earth = eph["earth"]
    body = eph[body_key]

    if observer is not None:
        pos = (earth + observer).at(t).observe(body).apparent()
    else:
        pos = earth.at(t).observe(body).apparent()

    lat, lon, _ = pos.ecliptic_latlon()
    return lon.degrees % 360


def calculate_planet_positions(birth_datetime: datetime, latitude: float, longitude: float,
                                include_extra_bodies: bool = True):
    """
    Calcula las posiciones eclípticas de Sol, Luna y planetas para un instante y lugar dados.
    birth_datetime debe estar en UTC (timezone-aware) o naive-asumido-UTC.

    Args:
        include_extra_bodies: si True (por defecto), añade también Nodo Norte,
            Nodo Sur, Lilith (Luna Negra media) y Quirón a la lista devuelta,
            tratados como PlanetPosition más para heredar automáticamente el
            cálculo de casas y aspectos.
    """
    ts = get_timescale()
    eph = get_ephemeris()

    if birth_datetime.tzinfo is None:
        birth_datetime = birth_datetime.replace(tzinfo=timezone.utc)

    t = ts.from_datetime(birth_datetime)
    observer_topo = wgs84.latlon(latitude, longitude)

    positions = []
    for name, key in PLANET_KEYS.items():
        lon_deg = _ecliptic_longitude(eph, key, t, observer=observer_topo)
        sign_data = longitude_to_sign(lon_deg)
        positions.append(PlanetPosition(
            name=name,
            longitude=lon_deg,
            sign=sign_data["sign"],
            degree_in_sign=sign_data["degree"],
        ))

    if include_extra_bodies:
        extra = calculate_extra_bodies(t)
        for body_data in extra.values():
            positions.append(PlanetPosition(
                name=body_data["name"],
                longitude=body_data["longitude"],
                sign=body_data["sign"],
                degree_in_sign=body_data["degree_in_sign"],
            ))

    return positions


def calculate_ascendant_mc(birth_datetime: datetime, latitude: float, longitude: float):
    """
    Calcula el Ascendente (ASC) y Medio Cielo (MC) usando fórmulas estándar
    de astrología esférica a partir del tiempo sidéreo local.
    """
    ts = get_timescale()
    eph = get_ephemeris()

    if birth_datetime.tzinfo is None:
        birth_datetime = birth_datetime.replace(tzinfo=timezone.utc)

    t = ts.from_datetime(birth_datetime)

    # Tiempo sidéreo local en grados
    gst = t.gast  # Greenwich Apparent Sidereal Time, en horas
    lst_hours = (gst + longitude / 15.0) % 24
    lst_deg = lst_hours * 15.0  # convertir a grados

    # Oblicuidad de la eclíptica (aprox., válido para época actual)
    d = t.tt - 2451545.0  # días desde J2000
    epsilon = 23.4393 - 3.563e-7 * d
    epsilon_rad = math.radians(epsilon)

    lat_rad = math.radians(latitude)
    lst_rad = math.radians(lst_deg)

    # Medio Cielo (MC): proyección del meridiano local sobre la eclíptica
    mc_rad = math.atan2(math.sin(lst_rad), math.cos(lst_rad) * math.cos(epsilon_rad))
    mc_deg = math.degrees(mc_rad) % 360

    # Ascendente: intersección del horizonte este con la eclíptica
    asc_rad = math.atan2(
        math.cos(lst_rad),
        -(math.sin(epsilon_rad) * math.tan(lat_rad) + math.cos(epsilon_rad) * math.sin(lst_rad))
    )
    asc_deg = math.degrees(asc_rad) % 360

    return asc_deg, mc_deg


def calculate_whole_sign_houses(ascendant_longitude: float):
    """
    Sistema de casas 'Whole Sign' (Casas Enteras): el signo del Ascendente
    ocupa toda la Casa 1, el siguiente signo toda la Casa 2, etc.
    Es el sistema más simple y robusto de calcular sin depender de latitudes extremas.
    """
    asc_sign_data = longitude_to_sign(ascendant_longitude)
    asc_sign_index = asc_sign_data["sign_index"]

    houses = []
    for i in range(12):
        sign_index = (asc_sign_index + i) % 12
        cusp_longitude = sign_index * 30.0
        sign_data = longitude_to_sign(cusp_longitude)
        houses.append(HouseCusp(
            house_number=i + 1,
            longitude=cusp_longitude,
            sign=sign_data["sign"],
        ))
    return houses


def assign_houses_to_planets(planets: list[PlanetPosition], houses: list[HouseCusp]):
    """Asigna cada planeta a su casa correspondiente (sistema Whole Sign)."""
    for planet in planets:
        planet_sign_index = int(planet.longitude // 30)
        for house in houses:
            house_sign_index = int(house.longitude // 30)
            if house_sign_index == planet_sign_index:
                planet.house = house.house_number
                break
    return planets


def calculate_aspects(planets: list[PlanetPosition]) -> list[Aspect]:
    """Calcula todos los aspectos mayores entre pares de planetas."""
    aspects = []
    for i in range(len(planets)):
        for j in range(i + 1, len(planets)):
            p1, p2 = planets[i], planets[j]
            diff = abs(p1.longitude - p2.longitude)
            if diff > 180:
                diff = 360 - diff

            for aspect_name, exact_angle, max_orb in MAJOR_ASPECTS:
                orb = abs(diff - exact_angle)
                if orb <= max_orb:
                    aspects.append(Aspect(
                        planet1=p1.name,
                        planet2=p2.name,
                        aspect_type=aspect_name,
                        orb=orb,
                        exact_angle=exact_angle,
                    ))
                    break  # un solo aspecto por par (el más cercano)
    return aspects


def calculate_birth_chart(
    birth_datetime: datetime,
    latitude: float,
    longitude: float,
    location_name: str = None,
) -> BirthChart:
    """
    Función principal: calcula la carta natal completa.

    Args:
        birth_datetime: fecha y hora de nacimiento en UTC (timezone-aware recomendado)
        latitude: latitud del lugar de nacimiento (-90 a 90)
        longitude: longitud del lugar de nacimiento (-180 a 180)
        location_name: nombre descriptivo del lugar (opcional)

    Returns:
        BirthChart con planetas, casas, ascendente, MC y aspectos calculados.
    """
    planets = calculate_planet_positions(birth_datetime, latitude, longitude)
    ascendant, midheaven = calculate_ascendant_mc(birth_datetime, latitude, longitude)
    houses = calculate_whole_sign_houses(ascendant)
    planets = assign_houses_to_planets(planets, houses)
    aspects = calculate_aspects(planets)

    return BirthChart(
        birth_datetime=birth_datetime,
        latitude=latitude,
        longitude=longitude,
        location_name=location_name,
        planets=planets,
        houses=houses,
        aspects=aspects,
        ascendant=ascendant,
        midheaven=midheaven,
    )
