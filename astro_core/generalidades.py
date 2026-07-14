"""
generalidades.py

Calcula el resumen de "Generalidades" de una carta natal: balance de
Elementos, Polaridades, Modalidades, y distribución por Hemisferios.

Se calcula únicamente sobre los 10 cuerpos "planetarios" clásicos (Sol a
Plutón) — Nodos, Lilith y Quirón quedan fuera de este resumen, siguiendo la
convención de la mayoría del software astrológico tradicional.
"""

from astro_core.models import BirthChart

_CLASSIC_PLANETS = [
    "sun", "moon", "mercury", "venus", "mars",
    "jupiter", "saturn", "uranus", "neptune", "pluto",
]

_ELEMENT_BY_SIGN = {
    "Aries": "fuego", "Leo": "fuego", "Sagitario": "fuego",
    "Tauro": "tierra", "Virgo": "tierra", "Capricornio": "tierra",
    "Géminis": "aire", "Libra": "aire", "Acuario": "aire",
    "Cáncer": "agua", "Escorpio": "agua", "Piscis": "agua",
}

_POLARITY_BY_ELEMENT = {
    "fuego": "positiva", "aire": "positiva",
    "tierra": "negativa", "agua": "negativa",
}

_MODALITY_BY_SIGN = {
    "Aries": "cardinal", "Cáncer": "cardinal", "Libra": "cardinal", "Capricornio": "cardinal",
    "Tauro": "fija", "Leo": "fija", "Escorpio": "fija", "Acuario": "fija",
    "Géminis": "mutable", "Virgo": "mutable", "Sagitario": "mutable", "Piscis": "mutable",
}


def calculate_generalidades(chart: BirthChart) -> dict:
    """
    Devuelve un dict con el desglose de:
      - elementos: {fuego, tierra, aire, agua} -> conteo
      - polaridades: {positiva, negativa} -> conteo
      - modalidades: {cardinal, fija, mutable} -> conteo
      - hemisferios: {superior_izq, superior_der, inferior_izq, inferior_der} -> conteo

    Convención de hemisferios (con Ascendente siempre a la izquierda):
      - superior_izq = casas 10, 11, 12
      - superior_der = casas 7, 8, 9
      - inferior_izq = casas 1, 2, 3
      - inferior_der = casas 4, 5, 6

    Solo cuenta los 10 planetas clásicos (Sol a Plutón) — no incluye Nodos,
    Lilith ni Quirón.
    """
    elementos = {"fuego": 0, "tierra": 0, "aire": 0, "agua": 0}
    polaridades = {"positiva": 0, "negativa": 0}
    modalidades = {"cardinal": 0, "fija": 0, "mutable": 0}
    hemisferios = {"superior_izq": 0, "superior_der": 0, "inferior_izq": 0, "inferior_der": 0}

    total = 0
    for planet in chart.planets:
        if planet.name not in _CLASSIC_PLANETS:
            continue
        total += 1

        elemento = _ELEMENT_BY_SIGN.get(planet.sign)
        if elemento:
            elementos[elemento] += 1
            polaridades[_POLARITY_BY_ELEMENT[elemento]] += 1

        modalidad = _MODALITY_BY_SIGN.get(planet.sign)
        if modalidad:
            modalidades[modalidad] += 1

        if planet.house is not None:
            if planet.house in (10, 11, 12):
                hemisferios["superior_izq"] += 1
            elif planet.house in (7, 8, 9):
                hemisferios["superior_der"] += 1
            elif planet.house in (1, 2, 3):
                hemisferios["inferior_izq"] += 1
            elif planet.house in (4, 5, 6):
                hemisferios["inferior_der"] += 1

    return {
        "total_planetas": total,
        "elementos": elementos,
        "polaridades": polaridades,
        "modalidades": modalidades,
        "hemisferios": hemisferios,
    }
