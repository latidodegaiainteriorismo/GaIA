"""
chart_svg.py
Genera una representación visual SVG de una carta natal, opcionalmente
con un anillo exterior de tránsitos superpuesto (bi-wheel).

No depende de librerías de gráficos pesadas — SVG es texto plano generado
a mano con trigonometría básica, así que no añade peso a producción.
"""

import math
from astro_core.models import BirthChart


# ── Constantes visuales ──────────────────────────────────────────────────

_SIZE = 640
_CENTER = _SIZE / 2
_R_OUTER = 280          # borde exterior del disco zodiacal (deja margen para etiqueta ASC)
_R_ZODIAC_IN = 248      # borde interior de la banda de signos

# Radios para carta natal SIMPLE (un solo anillo de planetas, más espacio disponible)
_R_SOLO_PLANETS = 165
_R_SOLO_HOUSES_IN = 130

# Radios para carta BI-WHEEL (natal + tránsitos, dos anillos que no deben solaparse)
_R_TRANSIT_RING = 225
_R_NATAL_PLANETS = 130
_R_HOUSES_IN = 95
_R_ASPECT_LINES = 95

_ZODIAC_SYMBOLS = ["♈", "♉", "♊", "♋", "♌", "♍", "♎", "♏", "♐", "♑", "♒", "♓"]
_PLANET_SYMBOLS = {
    "sun": "☉", "moon": "☽", "mercury": "☿", "venus": "♀", "mars": "♂",
    "jupiter": "♃", "saturn": "♄", "uranus": "♅", "neptune": "♆", "pluto": "♇",
    "north_node": "☊", "south_node": "☋", "lilith": "⚸", "chiron": "⚷",
}

_ASPECT_COLORS = {
    "conjunción": "#A0693A",
    "oposición": "#C96B6B",
    "cuadratura": "#C96B6B",
    "trígono": "#4A7B7E",
    "sextil": "#6B9EA0",
}

_TEAL = "#4A7B7E"
_TERRACOTA = "#A0693A"
_TEXT = "#4A4A46"
_BG = "#FAFAF8"


def _polar_to_xy(longitude_deg: float, radius: float, ascendant_deg: float = 0.0):
    """
    Convierte una longitud eclíptica en coordenadas x,y del SVG.
    El Ascendente se coloca siempre a la izquierda (posición de las 9 en punto),
    convención estándar en cartas astrológicas, y los signos avanzan en sentido
    antihorario (también convención estándar).
    """
    # Ángulo relativo al Ascendente, con 0° = izquierda (180° en coords SVG estándar)
    relative_angle = (longitude_deg - ascendant_deg) % 360
    # SVG: 0° = derecha, ángulos crecen en sentido horario. Invertimos para
    # que la carta gire antihorario como es convención astrológica.
    theta = math.radians(180 - relative_angle)
    x = _CENTER + radius * math.cos(theta)
    y = _CENTER - radius * math.sin(theta)
    return x, y


def _svg_header():
    return (
        f'<svg viewBox="0 0 {_SIZE} {_SIZE}" xmlns="http://www.w3.org/2000/svg" '
        f'font-family="Georgia, serif">'
        f'<rect width="{_SIZE}" height="{_SIZE}" fill="{_BG}"/>'
    )


def _draw_zodiac_ring(ascendant_deg: float) -> str:
    """Dibuja el anillo exterior con los 12 signos zodiacales."""
    parts = [
        f'<circle cx="{_CENTER}" cy="{_CENTER}" r="{_R_OUTER}" fill="none" stroke="{_TEAL}" stroke-width="1.5" opacity="0.5"/>',
        f'<circle cx="{_CENTER}" cy="{_CENTER}" r="{_R_ZODIAC_IN}" fill="none" stroke="{_TEAL}" stroke-width="1" opacity="0.35"/>',
    ]
    for i in range(12):
        sign_start = i * 30
        # Línea divisoria entre signos
        x1, y1 = _polar_to_xy(sign_start, _R_ZODIAC_IN, ascendant_deg)
        x2, y2 = _polar_to_xy(sign_start, _R_OUTER, ascendant_deg)
        parts.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                     f'stroke="{_TEAL}" stroke-width="0.75" opacity="0.3"/>')
        # Símbolo del signo, centrado en su banda de 30°
        symbol_x, symbol_y = _polar_to_xy(sign_start + 15, (_R_OUTER + _R_ZODIAC_IN) / 2, ascendant_deg)
        parts.append(f'<text x="{symbol_x:.1f}" y="{symbol_y:.1f}" font-size="18" fill="{_TERRACOTA}" '
                     f'text-anchor="middle" dominant-baseline="middle" opacity="0.85">{_ZODIAC_SYMBOLS[i]}</text>')
    return "".join(parts)


def _draw_houses(chart: BirthChart, ascendant_deg: float, r_houses_in: float) -> str:
    """Dibuja las 12 líneas de casas y sus números."""
    parts = [f'<circle cx="{_CENTER}" cy="{_CENTER}" r="{r_houses_in}" fill="none" '
             f'stroke="{_TEXT}" stroke-width="0.5" opacity="0.25"/>']
    for house in chart.houses:
        x1, y1 = _polar_to_xy(house.longitude, r_houses_in, ascendant_deg)
        x2, y2 = _polar_to_xy(house.longitude, _R_ZODIAC_IN, ascendant_deg)
        is_angle = house.house_number in (1, 4, 7, 10)  # ejes ASC/IC/DSC/MC, algo más marcados
        parts.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                     f'stroke="{_TEXT}" stroke-width="{1.2 if is_angle else 0.5}" opacity="{0.4 if is_angle else 0.2}"/>')
        num_x, num_y = _polar_to_xy(house.longitude + 15, r_houses_in - 18, ascendant_deg)
        parts.append(f'<text x="{num_x:.1f}" y="{num_y:.1f}" font-size="11" fill="{_TEXT}" '
                     f'text-anchor="middle" dominant-baseline="middle" opacity="0.4">{house.house_number}</text>')
    return "".join(parts)


def _draw_planets(planets, radius: float, ascendant_deg: float, color: str) -> str:
    """
    Dibuja símbolos de planetas en el radio dado. Reutilizable para natal y tránsito.
    Cuando dos o más planetas caen muy cerca en longitud, se desplazan radialmente
    (no angularmente) para evitar que los símbolos se solapen entre sí.
    """
    parts = []
    sorted_planets = sorted(planets, key=lambda p: p.longitude)

    # Agrupar planetas que están a menos de 7° entre sí (clusters de solapamiento)
    clusters = []
    current_cluster = []
    for p in sorted_planets:
        if not current_cluster:
            current_cluster = [p]
        elif p.longitude - current_cluster[-1].longitude < 7:
            current_cluster.append(p)
        else:
            clusters.append(current_cluster)
            current_cluster = [p]
    if current_cluster:
        clusters.append(current_cluster)

    for cluster in clusters:
        n = len(cluster)
        for i, p in enumerate(cluster):
            # Dentro de un cluster, escalonamos el radio ligeramente para separar
            # visualmente los símbolos sin desplazar su posición angular real.
            radial_offset = (i - (n - 1) / 2) * 16
            x, y = _polar_to_xy(p.longitude, radius + radial_offset, ascendant_deg)
            symbol = _PLANET_SYMBOLS.get(p.name, "?")
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="9" fill="{_BG}" opacity="0.85"/>')
            parts.append(f'<text x="{x:.1f}" y="{y:.1f}" font-size="15" fill="{color}" '
                         f'text-anchor="middle" dominant-baseline="central">{symbol}</text>')
    return "".join(parts)


def _draw_aspect_lines(chart: BirthChart, ascendant_deg: float, radius: float) -> str:
    """
    Dibuja las líneas de aspectos natales entre planetas, dentro del círculo de casas.
    Solo se dibujan aspectos con orbe ajustado (<=6°) para mantener la carta legible;
    el listado completo de aspectos sigue disponible en los datos, esto es solo la
    representación visual. La opacidad refleja la exactitud del aspecto.
    """
    parts = []
    for aspect in chart.aspects:
        if aspect.orb > 6:
            continue
        p1 = chart.get_planet(aspect.planet1)
        p2 = chart.get_planet(aspect.planet2)
        if not p1 or not p2:
            continue
        x1, y1 = _polar_to_xy(p1.longitude, radius, ascendant_deg)
        x2, y2 = _polar_to_xy(p2.longitude, radius, ascendant_deg)
        color = _ASPECT_COLORS.get(aspect.aspect_type, _TEXT)
        # Más exacto (orbe bajo) = línea más visible
        opacity = 0.55 - (aspect.orb / 6) * 0.3
        parts.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                     f'stroke="{color}" stroke-width="1" opacity="{opacity:.2f}"/>')
    return "".join(parts)


def _draw_transit_aspect_lines(transit_planets, natal_chart: BirthChart, transit_aspects: list[dict],
                                ascendant_deg: float) -> str:
    """Dibuja líneas entre planetas en tránsito (anillo exterior) y sus aspectos natales (anillo interior)."""
    parts = []
    transit_by_name = {p.name: p for p in transit_planets}
    for asp in transit_aspects:
        t_planet = transit_by_name.get(asp["transit_planet"])
        n_planet = natal_chart.get_planet(asp["natal_planet"])
        if not t_planet or not n_planet:
            continue
        x1, y1 = _polar_to_xy(t_planet.longitude, _R_TRANSIT_RING, ascendant_deg)
        x2, y2 = _polar_to_xy(n_planet.longitude, _R_NATAL_PLANETS, ascendant_deg)
        color = _ASPECT_COLORS.get(asp["aspect_type"], _TEXT)
        parts.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                     f'stroke="{color}" stroke-width="1" opacity="0.45" stroke-dasharray="3,2"/>')
    return "".join(parts)


def render_birth_chart_svg(chart: BirthChart) -> str:
    """
    Genera el SVG de una carta natal simple: rueda zodiacal + casas +
    planetas natales + líneas de aspectos.
    """
    ascendant_deg = chart.ascendant if chart.ascendant is not None else 0.0

    svg = [_svg_header()]
    svg.append(_draw_zodiac_ring(ascendant_deg))
    svg.append(_draw_houses(chart, ascendant_deg, _R_SOLO_HOUSES_IN))
    svg.append(_draw_aspect_lines(chart, ascendant_deg, _R_SOLO_HOUSES_IN))
    svg.append(_draw_planets(chart.planets, _R_SOLO_PLANETS, ascendant_deg, _TEAL))

    # Marca del Ascendente — pegada al borde interior del disco, con fondo para legibilidad
    asc_x, asc_y = _polar_to_xy(ascendant_deg, _R_OUTER + 16, ascendant_deg)
    asc_x = max(24, min(_SIZE - 24, asc_x))  # clamp dentro del viewBox
    svg.append(f'<rect x="{asc_x-16:.1f}" y="{asc_y-9:.1f}" width="32" height="18" rx="4" fill="{_BG}" opacity="0.9"/>')
    svg.append(f'<text x="{asc_x:.1f}" y="{asc_y:.1f}" font-size="12" fill="{_TERRACOTA}" '
               f'text-anchor="middle" dominant-baseline="central" font-weight="bold">ASC</text>')

    svg.append('</svg>')
    return "".join(svg)


def render_transit_chart_svg(natal_chart: BirthChart, transit_planets, transit_aspects: list[dict]) -> str:
    """
    Genera el SVG de una carta bi-wheel: anillo interior con la carta natal,
    anillo exterior con las posiciones en tránsito para la fecha solicitada,
    y líneas conectando los aspectos activos entre ambos.
    """
    ascendant_deg = natal_chart.ascendant if natal_chart.ascendant is not None else 0.0

    svg = [_svg_header()]
    svg.append(_draw_zodiac_ring(ascendant_deg))
    svg.append(_draw_houses(natal_chart, ascendant_deg, _R_HOUSES_IN))
    svg.append(_draw_transit_aspect_lines(transit_planets, natal_chart, transit_aspects, ascendant_deg))

    # Anillo de separación entre tránsitos (fuera) y natal (dentro)
    svg.append(f'<circle cx="{_CENTER}" cy="{_CENTER}" r="{(_R_NATAL_PLANETS + _R_TRANSIT_RING) / 2 + 5:.1f}" '
               f'fill="none" stroke="{_TEXT}" stroke-width="0.5" opacity="0.15" stroke-dasharray="2,3"/>')

    svg.append(_draw_planets(natal_chart.planets, _R_NATAL_PLANETS, ascendant_deg, _TEAL))
    svg.append(_draw_planets(transit_planets, _R_TRANSIT_RING, ascendant_deg, _TERRACOTA))

    asc_x, asc_y = _polar_to_xy(ascendant_deg, _R_OUTER + 16, ascendant_deg)
    asc_x = max(24, min(_SIZE - 24, asc_x))
    svg.append(f'<rect x="{asc_x-16:.1f}" y="{asc_y-9:.1f}" width="32" height="18" rx="4" fill="{_BG}" opacity="0.9"/>')
    svg.append(f'<text x="{asc_x:.1f}" y="{asc_y:.1f}" font-size="12" fill="{_TERRACOTA}" '
               f'text-anchor="middle" dominant-baseline="central" font-weight="bold">ASC</text>')

    # Leyenda simple: teal = natal, terracota = tránsito
    legend_y = _SIZE - 24
    svg.append(f'<circle cx="24" cy="{legend_y}" r="5" fill="{_TEAL}"/>')
    svg.append(f'<text x="36" y="{legend_y}" font-size="12" fill="{_TEXT}" dominant-baseline="middle" opacity="0.7">Natal</text>')
    svg.append(f'<circle cx="110" cy="{legend_y}" r="5" fill="{_TERRACOTA}"/>')
    svg.append(f'<text x="122" y="{legend_y}" font-size="12" fill="{_TEXT}" dominant-baseline="middle" opacity="0.7">Tránsito</text>')

    svg.append('</svg>')
    return "".join(svg)
