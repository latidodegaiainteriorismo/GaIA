"""
geocoding.py

Resuelve "Alicante, España" -> latitud, longitud, timezone.
Usa Nominatim (OpenStreetMap) para geocoding — gratis, sin API key.
Usa timezonefinder para resolver la zona horaria offline a partir de lat/lon
— también gratis, sin API key, sin llamadas de red.

Nominatim exige un User-Agent identificable y máximo 1 petición/segundo
(uso justo, sin API key). Para el volumen de GaIA (usuarios calculando su
carta natal, no un batch masivo) esto es más que suficiente.
"""

import logging
import requests
from timezonefinder import TimezoneFinder

logger = logging.getLogger(__name__)

_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_HEADERS = {"User-Agent": "GaIA-Astrologia/1.0 (contacto: gaia.conscienciaintegral@gmail.com)"}

_tf = None  # TimezoneFinder es costoso de instanciar — cacheado como singleton


def _get_tf():
    global _tf
    if _tf is None:
        _tf = TimezoneFinder()
    return _tf


def geocode_location(place_name: str) -> dict | None:
    """
    Convierte un nombre de lugar en coordenadas + timezone.

    Args:
        place_name: ej. "Alicante, España" o "Ciudad de México, México"

    Returns:
        dict con 'latitude', 'longitude', 'timezone', 'display_name' — o None si falla.
    """
    try:
        resp = requests.get(
            _NOMINATIM_URL,
            params={"q": place_name, "format": "json", "limit": 1},
            headers=_HEADERS,
            timeout=8,
        )
        resp.raise_for_status()
        results = resp.json()
        if not results:
            logger.warning(f"[geocoding] Sin resultados para '{place_name}'")
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
        logger.error(f"[geocoding] Error consultando Nominatim: {e}")
        return None
    except Exception as e:
        logger.error(f"[geocoding] Error inesperado: {e}")
        return None
