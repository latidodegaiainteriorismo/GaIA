"""
angulos.py
Los cuatro ángulos de la carta (Ascendente, Descendente, Medio Cielo y Fondo del Cielo)
como puntos con los que los planetas pueden formar aspectos, tanto en la carta natal
como en los tránsitos.

Solo tienen sentido si se conoce la hora de nacimiento: sin ella el Ascendente y el
Medio Cielo cambian de signo en pocas horas.
"""
from astro_core.models import longitude_to_sign

ASCENDENTE, DESCENDENTE, MEDIO_CIELO, FONDO_DEL_CIELO = (
    "ascendant", "descendant", "midheaven", "imum_coeli",
)
ANGULOS = (ASCENDENTE, DESCENDENTE, MEDIO_CIELO, FONDO_DEL_CIELO)
NOMBRES_ES = {
    ASCENDENTE: "Ascendente", DESCENDENTE: "Descendente",
    MEDIO_CIELO: "Medio Cielo", FONDO_DEL_CIELO: "Fondo del Cielo",
}

# (ángulo exacto, orbe) por aspecto. Más estrechos que entre planetas: los ángulos son
# puntos, no cuerpos, y un orbe amplio daría aspectos casi permanentes.
_ASPECTOS = (
    ("conjunción", 0, 6.0),
    ("cuadratura", 90, 5.0),
    ("trígono", 120, 4.0),
    ("sextil", 60, 3.0),
)


def puntos_angulares(ascendente: float, medio_cielo: float) -> list[dict]:
    """Los cuatro ángulos con signo, grado y casa (casas enteras: la 1 es el signo del Ascendente)."""
    lons = {
        ASCENDENTE: ascendente % 360,
        DESCENDENTE: (ascendente + 180) % 360,
        MEDIO_CIELO: medio_cielo % 360,
        FONDO_DEL_CIELO: (medio_cielo + 180) % 360,
    }
    signo_asc = int(lons[ASCENDENTE] // 30)
    puntos = []
    for nombre in ANGULOS:
        lon = lons[nombre]
        datos = longitude_to_sign(lon)
        puntos.append({
            "name": nombre,
            "longitude": lon,
            "sign": datos["sign"],
            "degree_in_sign": datos["degree"],
            "house": ((int(lon // 30) - signo_asc) % 12) + 1,
        })
    return puntos


def aspectos_a_angulos(longitud: float, puntos: list[dict], factor_orbe: float = 1.0) -> list[dict]:
    """
    Aspectos de un planeta (o planeta en tránsito) con los ángulos.

    - Conjunción con los cuatro ángulos (la conjunción con el Descendente es la oposición
      al Ascendente, y la del Fondo del Cielo la oposición al Medio Cielo; por eso no se
      busca la oposición aparte).
    - Cuadratura, trígono y sextil solo con el Ascendente y el Medio Cielo (con sus
      opuestos darían el mismo aspecto duplicado).
    - Un solo aspecto por pareja planeta-ángulo.
    """
    encontrados = []
    for p in puntos:
        for tipo, angulo, orbe_max in _ASPECTOS:
            if tipo != "conjunción" and p["name"] not in (ASCENDENTE, MEDIO_CIELO):
                continue
            diff = abs(longitud - p["longitude"]) % 360
            if diff > 180:
                diff = 360 - diff
            orbe = abs(diff - angulo)
            limite = orbe_max * factor_orbe
            if orbe <= limite:
                encontrados.append({
                    "point": p, "aspect_type": tipo, "orb": round(orbe, 2),
                    "exact_angle": angulo, "orb_max": limite,
                })
                break
    return encontrados
