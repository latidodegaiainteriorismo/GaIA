"""
geocoding.py

Resuelve lugar en texto -> latitud, longitud, timezone.
Usa LocationIQ (gratis, requiere API key, funciona bien desde servidores en la nube).
Usa timezonefinder para resolver zona horaria offline.
"""

import os
import logging
import requests
from timezonefinder import TimezoneFinder

logger = logging.getLogger(__name__)

_LOCATIONIQ_URL = "https://us1.locationiq.com/v1/search"
_LOCATIONIQ_KEY = os.environ.get("LOCATIONIQ_API_KEY", "")

_tf = None


def _get_tf():
    global _tf
    if _tf is None:
        _tf = TimezoneFinder()
    return _tf


def geocode_location(place_name):
    if not _LOCATIONIQ_KEY:
        logger.error("[geocoding] Falta LOCATIONIQ_API_KEY en variables de entorno")
        return None

    try:
        resp = requests.get(
            _LOCATIONIQ_URL,
            params={"key": _LOCATIONIQ_KEY, "q": place_name, "format": "json", "limit": 1},
            timeout=8,
        )
        resp.raise_for_status()
        results = resp.json()

        if not results:
            logger.warning(f"[geocoding] Sin resultados para {place_name}")
            return None

        lat = float(results[0]["lat"])
        lon = float(results[0]["lon"])
        timezone_name = _get_tf().timezone_at(lat=lat, lng=lon)

        return {
            "latitude": lat,
            "longitude": lon,
            "timezone": timezone_name or "UTC",
            "display_name": results[0].get("display_name", place_name),
        }

    except requests.RequestException as e:
        logger.error(f"[geocoding] Error consultando LocationIQ: {e}")
        return None
    except Exception as e:
        logger.error(f"[geocoding] Error inesperado: {e}")
        return None