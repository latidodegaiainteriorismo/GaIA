"""
ephemeris.py
Carga de datos astronómicos (efemérides) usando Skyfield + skyfield-data.
100% offline tras la instalación de skyfield-data (sin llamadas a NASA/JPL en runtime).
"""

from skyfield.api import load, wgs84
from skyfield_data import get_skyfield_data_path
import os

_ts = None
_eph = None


def get_timescale():
    """Devuelve el objeto Timescale de Skyfield (cacheado)."""
    global _ts
    if _ts is None:
        data_path = get_skyfield_data_path()
        # Usamos el finals2000A.all empaquetado para no golpear la red
        _ts = load.timescale(builtin=True)
    return _ts


def get_ephemeris():
    """Carga las efemérides planetarias DE421 (cacheado en memoria del proceso)."""
    global _eph
    if _eph is None:
        data_path = get_skyfield_data_path()
        bsp_path = os.path.join(data_path, "de421.bsp")
        _eph = load(bsp_path)
    return _eph


# Mapeo de nombres de planetas a claves de Skyfield en de421.bsp
PLANET_KEYS = {
    "sun": "sun",
    "moon": "moon",
    "mercury": "mercury",
    "venus": "venus",
    "mars": "mars",
    "jupiter": "jupiter barycenter",
    "saturn": "saturn barycenter",
    "uranus": "uranus barycenter",
    "neptune": "neptune barycenter",
    "pluto": "pluto barycenter",
}
