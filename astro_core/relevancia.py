"""
relevancia.py
Pesos para decidir qué aspectos importan más al leer una carta o unos tránsitos.

Antes los tránsitos se ordenaban solo por orbe, así que una Luna de paso (que cambia
de aspecto cada pocas horas) quedaba por delante de Plutón (que dura años) con solo
estar medio grado más exacta. Aquí se combinan tres cosas: lo lento que es el planeta
en tránsito, lo central que es el punto natal tocado y el tipo de aspecto, y se
escala todo por lo cerca que está de ser exacto.
"""

# Peso de quien transita: cuanto más lento, más profundo y duradero es su efecto.
PESO_TRANSITO = {
    "pluto": 10, "neptune": 9, "uranus": 9, "saturn": 8, "jupiter": 6,
    "chiron": 5, "north_node": 4, "south_node": 4,
    "mars": 3, "lilith": 2, "sun": 2, "mercury": 1.5, "venus": 1.5, "moon": 0.5,
}

# Peso del punto natal tocado: los luminares y los planetas personales pesan más.
PESO_NATAL = {
    "sun": 10, "moon": 10, "mercury": 7, "venus": 7, "mars": 7,
    "jupiter": 5, "saturn": 5, "uranus": 3, "neptune": 3, "pluto": 3,
    "chiron": 3, "north_node": 3, "south_node": 3, "lilith": 2,
    # Ángulos: puntos muy sensibles (Ascendente y Medio Cielo, a la altura de los personales)
    "ascendant": 9, "midheaven": 9, "descendant": 6, "imum_coeli": 6,
}

PESO_ASPECTO = {
    "conjunción": 1.0, "oposición": 1.0, "cuadratura": 0.9, "trígono": 0.7, "sextil": 0.5,
}

# Nivel de lectura de un tránsito según quién transita.
FONDO = {"pluto", "neptune", "uranus", "saturn"}
APOYO = {"jupiter", "chiron", "north_node", "south_node"}


def nivel_transito(planeta: str) -> str:
    """'fondo' (lentos: marcan el tema), 'apoyo' o 'disparador' (rápidos: marcan el momento)."""
    if planeta in FONDO:
        return "fondo"
    if planeta in APOYO:
        return "apoyo"
    return "disparador"


def _proximidad(orbe: float, orbe_max: float) -> float:
    """1.0 si es exacto, 0.0 si está en el límite del orbe."""
    if orbe_max <= 0:
        return 0.0
    return max(0.0, 1.0 - orbe / orbe_max)


def puntuar_transito(planeta_transito: str, planeta_natal: str, aspecto: str,
                     orbe: float, orbe_max: float) -> float:
    base = (PESO_TRANSITO.get(planeta_transito, 2)
            * PESO_NATAL.get(planeta_natal, 2)
            * PESO_ASPECTO.get(aspecto, 0.5))
    return round(base * (0.3 + 0.7 * _proximidad(orbe, orbe_max)), 2)


def puntuar_aspecto_natal(planeta1: str, planeta2: str, aspecto: str,
                          orbe: float, orbe_max: float) -> float:
    base = (PESO_NATAL.get(planeta1, 2) * PESO_NATAL.get(planeta2, 2)
            * PESO_ASPECTO.get(aspecto, 0.5))
    return round(base * (0.3 + 0.7 * _proximidad(orbe, orbe_max)), 2)
