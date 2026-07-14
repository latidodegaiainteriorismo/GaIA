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
# NOTA (jul-2026 v3): añadido "hub" central — las casas ahora son radios
# completos desde un pequeño círculo interior hasta el anillo zodiacal, y
# los aspectos se dibujan como telaraña convergiendo en ese mismo hub
# (estilo carta profesional clásica), en vez de conectar los planetas
# directamente. También se añade el eje ASC-DSC / MC-IC marcado en rojo.

_SIZE = 760
_CENTER = _SIZE / 2
_R_OUTER = 330          # borde exterior del disco zodiacal
_R_ZODIAC_IN = 288      # borde interior de la banda de signos

# Hub central — donde convergen las líneas de aspectos y empiezan las casas
_R_HUB_SOLO = 55
_R_HUB_BIWHEEL = 42

# Radios para carta natal SIMPLE
_R_SOLO_PLANETS = 205
_R_SOLO_HOUSE_LABELS = 265   # radio donde se ponen los números de casa, pegados al anillo zodiacal

# Radios para carta BI-WHEEL (natal + tránsitos)
_R_TRANSIT_RING = 258
_R_NATAL_PLANETS = 150
_R_HOUSES_IN = 105
_R_HOUSE_LABELS_BIWHEEL = 118

_ZODIAC_SYMBOLS = ["♈", "♉", "♊", "♋", "♌", "♍", "♎", "♏", "♐", "♑", "♒", "♓"]
_PLANET_SYMBOLS = {
    "sun": "☉", "moon": "☽", "mercury": "☿", "venus": "♀", "mars": "♂",
    "jupiter": "♃", "saturn": "♄", "uranus": "♅", "neptune": "♆", "pluto": "♇",
    "north_node": "☊", "south_node": "☋", "lilith": "⚸", "chiron": "⚷",
}

_ASPECT_COLORS = {
    "conjunción": "#A0693A",
    "oposición": "#C24B4B",
    "cuadratura": "#C24B4B",
    "trígono": "#3F7A7D",
    "sextil": "#5B9C9E",
}

_ASPECT_WIDTHS = {
    "conjunción": 2.2,
    "oposición": 2.2,
    "cuadratura": 2.0,
    "trígono": 1.6,
    "sextil": 1.4,
}

_TEAL = "#4A7B7E"
_TERRACOTA = "#A0693A"
_TEXT = "#4A4A46"
_BG = "#FAFAF8"
_AXIS_RED = "#B23A3A"


def _polar_to_xy(longitude_deg: float, radius: float, ascendant_deg: float = 0.0):
    """
    Convierte una longitud eclíptica en coordenadas x,y del SVG.
    El Ascendente se coloca siempre a la izquierda (posición de las 9 en punto),
    convención estándar en cartas astrológicas, y los signos avanzan en sentido
    antihorario (también convención estándar).
    """
    relative_angle = (longitude_deg - ascendant_deg) % 360
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
        f'<circle cx="{_CENTER}" cy="{_CENTER}" r="{_R_OUTER}" fill="none" stroke="{_TEAL}" stroke-width="2" opacity="0.55"/>',
        f'<circle cx="{_CENTER}" cy="{_CENTER}" r="{_R_ZODIAC_IN}" fill="none" stroke="{_TEAL}" stroke-width="1.5" opacity="0.4"/>',
    ]
    for i in range(12):
        sign_start = i * 30
        x1, y1 = _polar_to_xy(sign_start, _R_ZODIAC_IN, ascendant_deg)
        x2, y2 = _polar_to_xy(sign_start, _R_OUTER, ascendant_deg)
        parts.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                     f'stroke="{_TEAL}" stroke-width="1" opacity="0.35"/>')
        symbol_x, symbol_y = _polar_to_xy(sign_start + 15, (_R_OUTER + _R_ZODIAC_IN) / 2, ascendant_deg)
        parts.append(f'<text x="{symbol_x:.1f}" y="{symbol_y:.1f}" font-size="30" fill="{_TERRACOTA}" '
                     f'text-anchor="middle" dominant-baseline="middle" opacity="0.9">{_ZODIAC_SYMBOLS[i]}</text>')
    return "".join(parts)


def _draw_houses(chart: BirthChart, ascendant_deg: float, r_hub: float, r_label: float) -> str:
    """
    Dibuja las 12 líneas de casas como radios completos, desde el hub central
    hasta el anillo zodiacal, con su número de casa cerca del anillo exterior.
    """
    parts = [f'<circle cx="{_CENTER}" cy="{_CENTER}" r="{r_hub}" fill="none" '
             f'stroke="{_TEXT}" stroke-width="0.75" opacity="0.35"/>']
    for house in chart.houses:
        x1, y1 = _polar_to_xy(house.longitude, r_hub, ascendant_deg)
        x2, y2 = _polar_to_xy(house.longitude, _R_ZODIAC_IN, ascendant_deg)
        is_angle = house.house_number in (1, 4, 7, 10)  # ejes ASC/IC/DSC/MC
        parts.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                     f'stroke="{_TEXT}" stroke-width="{1.6 if is_angle else 0.75}" opacity="{0.45 if is_angle else 0.25}"/>')
        num_x, num_y = _polar_to_xy(house.longitude + 15, r_label, ascendant_deg)
        parts.append(f'<text x="{num_x:.1f}" y="{num_y:.1f}" font-size="14" fill="{_TEXT}" '
                     f'text-anchor="middle" dominant-baseline="middle" opacity="0.55" font-weight="bold">{house.house_number}</text>')
    return "".join(parts)


def _draw_angle_axes(ascendant_deg: float, midheaven_deg: float | None) -> str:
    """
    Dibuja el eje ASC-DSC (siempre la línea horizontal en nuestra convención,
    ya que el Ascendente se ancla a la izquierda) y el eje MC-IC, ambos en
    rojo y atravesando toda la carta, como en una carta profesional clásica.
    """
    parts = []
    asc_x1, asc_y1 = _polar_to_xy(ascendant_deg, _R_OUTER, ascendant_deg)
    asc_x2, asc_y2 = _polar_to_xy(ascendant_deg + 180, _R_OUTER, ascendant_deg)
    parts.append(f'<line x1="{asc_x1:.1f}" y1="{asc_y1:.1f}" x2="{asc_x2:.1f}" y2="{asc_y2:.1f}" '
                 f'stroke="{_AXIS_RED}" stroke-width="1.6" opacity="0.5"/>')

    if midheaven_deg is not None:
        mc_x1, mc_y1 = _polar_to_xy(midheaven_deg, _R_OUTER, ascendant_deg)
        mc_x2, mc_y2 = _polar_to_xy(midheaven_deg + 180, _R_OUTER, ascendant_deg)
        parts.append(f'<line x1="{mc_x1:.1f}" y1="{mc_y1:.1f}" x2="{mc_x2:.1f}" y2="{mc_y2:.1f}" '
                     f'stroke="{_AXIS_RED}" stroke-width="1.6" opacity="0.5"/>')
    return "".join(parts)


def _draw_planets(planets, radius: float, ascendant_deg: float, color: str) -> str:
    """
    Dibuja símbolos de planetas en el radio dado. Reutilizable para natal y tránsito.
    Cuando dos o más planetas caen muy cerca en longitud, se desplazan radialmente
    (no angularmente) para evitar que los símbolos se solapen entre sí.
    Cada símbolo lleva debajo su grado exacto dentro del signo, en texto pequeño.
    """
    parts = []
    sorted_planets = sorted(planets, key=lambda p: p.longitude)

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
            radial_offset = (i - (n - 1) / 2) * 22
            x, y = _polar_to_xy(p.longitude, radius + radial_offset, ascendant_deg)
            symbol = _PLANET_SYMBOLS.get(p.name, "?")
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="14" fill="{_BG}" opacity="0.92"/>')
            parts.append(f'<text x="{x:.1f}" y="{y:.1f}" font-size="24" fill="{color}" '
                         f'text-anchor="middle" dominant-baseline="central" font-weight="bold">{symbol}</text>')
            retro_marker = "℞" if getattr(p, "is_retrograde", False) else ""
            deg_x, deg_y = _polar_to_xy(p.longitude, radius + radial_offset + 20, ascendant_deg)
            parts.append(f'<text x="{deg_x:.1f}" y="{deg_y:.1f}" font-size="10" fill="{_TEXT}" '
                         f'text-anchor="middle" dominant-baseline="middle" opacity="0.65">'
                         f'{round(p.degree_in_sign)}°{retro_marker}</text>')
    return "".join(parts)


def _draw_aspect_web(chart: BirthChart, ascendant_deg: float, r_hub: float) -> str:
    """
    Dibuja las líneas de aspectos natales como una telaraña que converge en
    el hub central — cada línea une los puntos del hub correspondientes a la
    posición angular de cada planeta, en vez de unir los símbolos directamente.
    Solo se dibujan aspectos con orbe ajustado (<=7°) para mantener la carta legible.
    """
    parts = []
    for aspect in chart.aspects:
        if aspect.orb > 7:
            continue
        p1 = chart.get_planet(aspect.planet1)
        p2 = chart.get_planet(aspect.planet2)
        if not p1 or not p2:
            continue
        x1, y1 = _polar_to_xy(p1.longitude, r_hub, ascendant_deg)
        x2, y2 = _polar_to_xy(p2.longitude, r_hub, ascendant_deg)
        color = _ASPECT_COLORS.get(aspect.aspect_type, _TEXT)
        width = _ASPECT_WIDTHS.get(aspect.aspect_type, 1.5)
        opacity = 0.85 - (aspect.orb / 7) * 0.4
        parts.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                     f'stroke="{color}" stroke-width="{width}" opacity="{opacity:.2f}"/>')
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
        width = _ASPECT_WIDTHS.get(asp["aspect_type"], 1.5)
        parts.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                     f'stroke="{color}" stroke-width="{width}" opacity="0.6" stroke-dasharray="4,3"/>')
    return "".join(parts)


def _draw_legend(aspects_present: set) -> str:
    """Pequeña leyenda de colores de aspectos, solo con los tipos presentes en esta carta."""
    if not aspects_present:
        return ""
    parts = []
    x = 24
    y = 28
    for aspect_type in ["conjunción", "trígono", "sextil", "cuadratura", "oposición"]:
        if aspect_type not in aspects_present:
            continue
        color = _ASPECT_COLORS.get(aspect_type, _TEXT)
        parts.append(f'<line x1="{x}" y1="{y}" x2="{x+20}" y2="{y}" stroke="{color}" stroke-width="2.2"/>')
        parts.append(f'<text x="{x+26}" y="{y}" font-size="12" fill="{_TEXT}" '
                     f'dominant-baseline="middle" opacity="0.75">{aspect_type.capitalize()}</text>')
        y += 20
    return "".join(parts)


def render_birth_chart_svg(chart: BirthChart) -> str:
    """
    Genera el SVG de una carta natal simple: rueda zodiacal + casas +
    eje ASC/MC + telaraña de aspectos + planetas natales.
    """
    ascendant_deg = chart.ascendant if chart.ascendant is not None else 0.0

    svg = [_svg_header()]
    svg.append(_draw_zodiac_ring(ascendant_deg))
    svg.append(_draw_houses(chart, ascendant_deg, _R_HUB_SOLO, _R_SOLO_HOUSE_LABELS))
    svg.append(_draw_angle_axes(ascendant_deg, chart.midheaven))
    svg.append(_draw_aspect_web(chart, ascendant_deg, _R_HUB_SOLO))
    svg.append(_draw_planets(chart.planets, _R_SOLO_PLANETS, ascendant_deg, _TEAL))

    asc_x, asc_y = _polar_to_xy(ascendant_deg, _R_OUTER + 20, ascendant_deg)
    asc_x = max(28, min(_SIZE - 28, asc_x))
    svg.append(f'<rect x="{asc_x-20:.1f}" y="{asc_y-11:.1f}" width="40" height="22" rx="5" fill="{_BG}" opacity="0.92"/>')
    svg.append(f'<text x="{asc_x:.1f}" y="{asc_y:.1f}" font-size="15" fill="{_TERRACOTA}" '
               f'text-anchor="middle" dominant-baseline="central" font-weight="bold">ASC</text>')

    aspects_present = {a.aspect_type for a in chart.aspects if a.orb <= 7}
    svg.append(_draw_legend(aspects_present))

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
    svg.append(_draw_houses(natal_chart, ascendant_deg, _R_HUB_BIWHEEL, _R_HOUSE_LABELS_BIWHEEL))
    svg.append(_draw_angle_axes(ascendant_deg, natal_chart.midheaven))
    svg.append(_draw_transit_aspect_lines(transit_planets, natal_chart, transit_aspects, ascendant_deg))

    svg.append(f'<circle cx="{_CENTER}" cy="{_CENTER}" r="{(_R_NATAL_PLANETS + _R_TRANSIT_RING) / 2 + 6:.1f}" '
               f'fill="none" stroke="{_TEXT}" stroke-width="0.75" opacity="0.2" stroke-dasharray="3,4"/>')

    svg.append(_draw_planets(natal_chart.planets, _R_NATAL_PLANETS, ascendant_deg, _TEAL))
    svg.append(_draw_planets(transit_planets, _R_TRANSIT_RING, ascendant_deg, _TERRACOTA))

    asc_x, asc_y = _polar_to_xy(ascendant_deg, _R_OUTER + 20, ascendant_deg)
    asc_x = max(28, min(_SIZE - 28, asc_x))
    svg.append(f'<rect x="{asc_x-20:.1f}" y="{asc_y-11:.1f}" width="40" height="22" rx="5" fill="{_BG}" opacity="0.92"/>')
    svg.append(f'<text x="{asc_x:.1f}" y="{asc_y:.1f}" font-size="15" fill="{_TERRACOTA}" '
               f'text-anchor="middle" dominant-baseline="central" font-weight="bold">ASC</text>')

    legend_y = _SIZE - 28
    svg.append(f'<circle cx="28" cy="{legend_y}" r="6.5" fill="{_TEAL}"/>')
    svg.append(f'<text x="42" y="{legend_y}" font-size="14" fill="{_TEXT}" dominant-baseline="middle" opacity="0.75">Natal</text>')
    svg.append(f'<circle cx="130" cy="{legend_y}" r="6.5" fill="{_TERRACOTA}"/>')
    svg.append(f'<text x="144" y="{legend_y}" font-size="14" fill="{_TEXT}" dominant-baseline="middle" opacity="0.75">Tránsito</text>')

    svg.append('</svg>')
    return "".join(svg)
