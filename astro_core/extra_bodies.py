"""
extra_bodies.py

Calcula puntos y cuerpos adicionales usados en astrología pero no incluidos
en de421.bsp: Nodo Norte/Sur lunar, Lilith (Luna Negra media), y Quirón.

- Nodo Lunar y Lilith media: derivados matemáticamente de los elementos
  orbitales medios de la Luna (fórmulas de Jean Meeus, Astronomical
  Algorithms, cap. 47). No requieren datos externos ni tienen error de
  "época" — son válidos para cualquier fecha con la misma precisión.

- Quirón: NO está en de421.bsp (solo cubre Sol, planetas y Luna). Se calcula
  como órbita kepleriana a partir de sus elementos orbitales osculantes
  (JPL Small-Body Database, época 2026-06-08). Esto es una aproximación:
  la órbita de Quirón está perturbada por Saturno y Urano, así que la
  precisión se degrada gradualmente cuanto más lejos esté la fecha
  calculada de la época de referencia. Para fechas dentro de +/- 10 años
  de 2026 el error es del orden de fracciones de grado — más que suficiente
  para uso astrológico. Para cartas muy antiguas (décadas) puede desviarse
  varios grados; se documenta así en el bloque de contexto para GaIA.
"""

import math
from skyfield.data.mpc import _KeplerOrbit
from skyfield.constants import GM_SUN_Pitjeva_2005_km3_s2
from astro_core.ephemeris import get_timescale, get_ephemeris
from astro_core.models import longitude_to_sign


# ── Nodo Lunar y Lilith media (fórmulas de Meeus) ───────────────────────

def calculate_lunar_node(t) -> dict:
    """
    Nodo Norte lunar medio (el Nodo Sur es siempre el punto opuesto, 180°).
    t: objeto Time de Skyfield.
    """
    T = (t.tt - 2451545.0) / 36525.0  # siglos julianos desde J2000
    omega = 125.0445479 - 1934.1362891*T + 0.0020754*T**2 + T**3/467441 - T**4/60616000
    node_north = omega % 360
    node_south = (node_north + 180) % 360
    return {"north": node_north, "south": node_south}


def calculate_lilith_mean(t) -> float:
    """Lilith media (apogeo lunar medio), en grados de longitud eclíptica."""
    T = (t.tt - 2451545.0) / 36525.0
    perigee = 83.3532465 + 4069.0137287*T - 0.0103200*T**2 - T**3/80053 + T**4/18999000
    apogee = (perigee + 180) % 360
    return apogee


# ── Quirón (órbita kepleriana aproximada) ───────────────────────────────

# Elementos orbitales osculantes de 2060 Chiron.
# Época: JD 2461200.5 (2026-06-08 00:00 UTC). Fuente: JPL Small-Body Database.
_CHIRON_EPOCH_JD = 2461200.5
_CHIRON_A = 13.7        # semieje mayor, AU
_CHIRON_E = 0.3774      # excentricidad
_CHIRON_INCL = 6.92     # inclinación, grados
_CHIRON_NODE = 209.31   # longitud del nodo ascendente, grados
_CHIRON_PERI = 339.36   # argumento del perihelio, grados
_CHIRON_M0 = 196.94     # anomalía media en la época, grados

_chiron_orbit_cache = None


def _get_chiron_orbit(ts):
    """Construye (una vez, cacheado) el objeto de órbita kepleriana de Quirón."""
    global _chiron_orbit_cache
    if _chiron_orbit_cache is None:
        p = _CHIRON_A * (1 - _CHIRON_E ** 2)
        t_epoch = ts.tt_jd(_CHIRON_EPOCH_JD)
        _chiron_orbit_cache = _KeplerOrbit._from_mean_anomaly(
            p, _CHIRON_E, _CHIRON_INCL, _CHIRON_NODE, _CHIRON_PERI, _CHIRON_M0,
            t_epoch, GM_SUN_Pitjeva_2005_km3_s2, 10, 'Chiron'
        )
    return _chiron_orbit_cache


def calculate_chiron_longitude(t) -> float:
    """Longitud eclíptica geocéntrica aparente de Quirón en el instante t."""
    ts = get_timescale()
    eph = get_ephemeris()
    orbit = _get_chiron_orbit(ts)

    sun = eph['sun']
    earth = eph['earth']
    chiron = sun + orbit

    astrometric = earth.at(t).observe(chiron).apparent()
    _, lon, _ = astrometric.ecliptic_latlon()
    return lon.degrees % 360


# ── Interfaz unificada ───────────────────────────────────────────────────

def calculate_extra_bodies(t) -> dict:
    """
    Calcula todos los cuerpos/puntos adicionales para un instante dado.
    Devuelve un dict con longitud eclíptica y datos de signo para cada uno,
    en el mismo formato que PlanetPosition.to_dict() para consistencia.
    """
    node = calculate_lunar_node(t)
    lilith = calculate_lilith_mean(t)
    chiron = calculate_chiron_longitude(t)

    def _entry(name, longitude):
        sign_data = longitude_to_sign(longitude)
        return {
            "name": name,
            "longitude": longitude,
            "sign": sign_data["sign"],
            "degree_in_sign": sign_data["degree"],
            "is_retrograde": False,  # no calculado para estos puntos (ver nota abajo)
        }

    return {
        "north_node": _entry("north_node", node["north"]),
        "south_node": _entry("south_node", node["south"]),
        "lilith": _entry("lilith", lilith),
        "chiron": _entry("chiron", chiron),
    }
