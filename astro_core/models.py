"""
models.py
Estructuras de datos para representar cartas astrales, posiciones planetarias,
casas astrológicas y aspectos entre planetas.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


ZODIAC_SIGNS = [
    "Aries", "Tauro", "Géminis", "Cáncer", "Leo", "Virgo",
    "Libra", "Escorpio", "Sagitario", "Capricornio", "Acuario", "Piscis"
]


def longitude_to_sign(longitude: float) -> dict:
    """
    Convierte una longitud eclíptica (0-360°) en signo zodiacal + grado dentro del signo.
    """
    longitude = longitude % 360
    sign_index = int(longitude // 30)
    degree_in_sign = longitude % 30
    return {
        "sign": ZODIAC_SIGNS[sign_index],
        "sign_index": sign_index,
        "degree": round(degree_in_sign, 2),
        "absolute_longitude": round(longitude, 4),
    }


@dataclass
class PlanetPosition:
    name: str                  # "sun", "moon", "mercury", etc.
    longitude: float           # longitud eclíptica en grados (0-360)
    sign: str                  # signo zodiacal
    degree_in_sign: float      # grados dentro del signo (0-30)
    is_retrograde: bool = False
    house: Optional[int] = None  # casa astrológica (1-12), se añade después

    def to_dict(self):
        return {
            "name": self.name,
            "longitude": self.longitude,
            "sign": self.sign,
            "degree_in_sign": self.degree_in_sign,
            "is_retrograde": self.is_retrograde,
            "house": self.house,
        }


@dataclass
class HouseCusp:
    house_number: int          # 1-12
    longitude: float           # longitud eclíptica de la cúspide
    sign: str

    def to_dict(self):
        return {
            "house_number": self.house_number,
            "longitude": self.longitude,
            "sign": self.sign,
        }


@dataclass
class Aspect:
    planet1: str
    planet2: str
    aspect_type: str    # "conjunción", "oposición", "trígono", "cuadratura", "sextil"
    orb: float          # diferencia en grados respecto al aspecto exacto
    exact_angle: float  # el ángulo teórico del aspecto (0, 60, 90, 120, 180)

    def to_dict(self):
        return {
            "planet1": self.planet1,
            "planet2": self.planet2,
            "aspect_type": self.aspect_type,
            "orb": round(self.orb, 2),
            "exact_angle": self.exact_angle,
        }


@dataclass
class BirthChart:
    birth_datetime: datetime
    latitude: float
    longitude: float
    location_name: Optional[str] = None
    planets: list[PlanetPosition] = field(default_factory=list)
    houses: list[HouseCusp] = field(default_factory=list)
    aspects: list[Aspect] = field(default_factory=list)
    ascendant: Optional[float] = None   # longitud eclíptica del Ascendente
    midheaven: Optional[float] = None   # longitud eclíptica del Medio Cielo (MC)

    def get_planet(self, name: str) -> Optional[PlanetPosition]:
        for p in self.planets:
            if p.name == name:
                return p
        return None

    def to_dict(self):
        asc_data = longitude_to_sign(self.ascendant) if self.ascendant is not None else None
        mc_data = longitude_to_sign(self.midheaven) if self.midheaven is not None else None
        return {
            "birth_datetime": self.birth_datetime.isoformat(),
            "latitude": self.latitude,
            "longitude": self.longitude,
            "location_name": self.location_name,
            "planets": [p.to_dict() for p in self.planets],
            "houses": [h.to_dict() for h in self.houses],
            "aspects": [a.to_dict() for a in self.aspects],
            "ascendant": asc_data,
            "midheaven": mc_data,
        }
