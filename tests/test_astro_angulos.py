"""
Precisión astronómica (zodiaco de la fecha, geocéntrico), ángulos, retrógrados y carta sin hora.
"""
from datetime import datetime, timezone

import pytest

import astrology
from astro_core import angulos, transits
from astro_core.birth_chart import calculate_ascendant_mc, calculate_planet_positions, _is_retrograde
from astro_core.ephemeris import get_ephemeris, get_timescale

_SIGNOS = ["Aries", "Tauro", "Géminis", "Cáncer", "Leo", "Virgo", "Libra", "Escorpio",
           "Sagitario", "Capricornio", "Acuario", "Piscis"]


def _lon(signo, grados, minutos, segundos):
    return _SIGNOS.index(signo) * 30 + grados + minutos / 60 + segundos / 3600


# ── Precisión: contraste con una efeméride externa (carta-natal.es, 3-nov-2026 12:05:41 UT,
#    tropical y geocéntrico). Antes de corregir salían ~0,36° bajos (Luna: casi 1°). ──────────

_REFERENCIA = {
    "sun": _lon("Escorpio", 11, 4, 58), "moon": _lon("Virgo", 2, 2, 44),
    "mercury": _lon("Escorpio", 13, 36, 43), "venus": _lon("Libra", 25, 6, 4),
    "mars": _lon("Leo", 19, 52, 21), "jupiter": _lon("Leo", 24, 37, 13),
    "saturn": _lon("Aries", 9, 7, 56), "uranus": _lon("Géminis", 4, 35, 1),
    "neptune": _lon("Aries", 2, 1, 57), "pluto": _lon("Acuario", 3, 8, 56),
    "north_node": _lon("Acuario", 25, 56, 28),
}
_MOMENTO = datetime(2026, 11, 3, 12, 5, 41, tzinfo=timezone.utc)


def _dif(a, b):
    return abs(((a - b + 180) % 360) - 180)


def test_posiciones_coinciden_con_la_efemeride_externa():
    posiciones = {p.name: p.longitude for p in calculate_planet_positions(_MOMENTO, 40.4, -3.7)}
    for nombre, esperado in _REFERENCIA.items():
        assert _dif(posiciones[nombre], esperado) < 0.025, nombre


def test_el_sol_esta_en_0_de_aries_en_el_equinoccio():
    sol = calculate_planet_positions(datetime(2026, 3, 20, 14, 46, tzinfo=timezone.utc), 0, 0)[0]
    assert _dif(sol.longitude, 0.0) < 0.05


def test_las_posiciones_no_dependen_del_lugar_del_observador():
    madrid = {p.name: p.longitude for p in calculate_planet_positions(_MOMENTO, 40.4, -3.7)}
    sidney = {p.name: p.longitude for p in calculate_planet_positions(_MOMENTO, -33.9, 151.2)}
    assert madrid["moon"] == pytest.approx(sidney["moon"], abs=1e-6)


# ── Retrógrados ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("fecha, retrogrado", [
    (datetime(2022, 12, 15, 12, tzinfo=timezone.utc), True),    # Marte retrógrado (oct-22 a ene-23)
    (datetime(2022, 6, 1, 12, tzinfo=timezone.utc), False),
])
def test_marte_retrogrado(fecha, retrogrado):
    ts, eph = get_timescale(), get_ephemeris()
    assert _is_retrograde(eph, "mars", ts.from_datetime(fecha), "mars") is retrogrado


def test_sol_y_luna_nunca_son_retrogrados():
    ts, eph = get_timescale(), get_ephemeris()
    t = ts.from_datetime(_MOMENTO)
    assert _is_retrograde(eph, "sun", t, "sun") is False
    assert _is_retrograde(eph, "moon", t, "moon") is False


# ── Ángulos ──────────────────────────────────────────────────────────────────

def test_ascendente_y_medio_cielo_de_madrid_1985():
    # Verificados con un cálculo independiente (muestreo del horizonte), diferencia < 0,01°
    asc, mc = calculate_ascendant_mc(datetime(1985, 6, 15, 10, 30, tzinfo=timezone.utc), 40.4168, -3.7038)
    assert asc == pytest.approx(154.18, abs=0.02)
    assert mc == pytest.approx(59.64, abs=0.02)


def test_los_cuatro_angulos_con_signo_y_casa():
    pts = {p["name"]: p for p in angulos.puntos_angulares(154.18, 59.64)}
    assert (pts["ascendant"]["sign"], pts["ascendant"]["house"]) == ("Virgo", 1)
    assert (pts["descendant"]["sign"], pts["descendant"]["house"]) == ("Piscis", 7)
    assert (pts["midheaven"]["sign"], pts["midheaven"]["house"]) == ("Tauro", 9)
    assert (pts["imum_coeli"]["sign"], pts["imum_coeli"]["house"]) == ("Escorpio", 3)


def _tipos(longitud, asc=154.18, mc=59.64, factor=1.0):
    pts = angulos.puntos_angulares(asc, mc)
    return {(a["point"]["name"], a["aspect_type"]): a["orb"]
            for a in angulos.aspectos_a_angulos(longitud, pts, factor)}


def test_conjuncion_con_el_ascendente():
    assert _tipos(154.18 + 2.0) == {("ascendant", "conjunción"): 2.0}


def test_la_oposicion_al_ascendente_se_da_como_conjuncion_al_descendente():
    assert _tipos(154.18 + 180 + 1.0) == {("descendant", "conjunción"): 1.0}


def test_cuadratura_al_ascendente_y_fuera_de_orbe():
    assert ("ascendant", "cuadratura") in _tipos(154.18 + 91.0, mc=10.0)
    assert _tipos(154.18 + 20.0, mc=200.0) == {}


def test_los_transitos_usan_orbes_mas_estrechos():
    assert _tipos(154.18 + 4.0) != {}
    assert _tipos(154.18 + 4.0, factor=0.6) == {}


# ── Tránsitos a los ángulos y casa de tránsito ───────────────────────────────

class _P:
    def __init__(self, name, lon, house=1):
        self.name, self.longitude, self.sign, self.house = name, lon, "Aries", house
        self.is_retrograde = False


def _natal(time_known=True):
    return type("C", (), {"planets": [_P("sun", 200.0)], "latitude": 0, "longitude": 0,
                          "ascendant": 100.0, "midheaven": 10.0, "time_known": time_known})()


def test_un_lento_sobre_el_ascendente_aparece_solo_si_se_piden_los_angulos():
    plutón = [_P("pluto", 101.0)]
    sin = transits.calculate_transit_aspects(_natal(), plutón)
    con = transits.calculate_transit_aspects(_natal(), plutón, include_angles=True)
    assert not any(a["natal_planet"] == "ascendant" for a in sin)
    asp = [a for a in con if a["natal_planet"] == "ascendant"][0]
    assert asp["aspect_type"] == "conjunción" and asp["nivel"] == "fondo"
    assert asp["transit_house"] == 1 and asp["natal_house"] == 1


def test_la_casa_de_transito_cuenta_desde_el_ascendente():
    # Ascendente en 100° (Cáncer, signo 3): un planeta en Virgo (signo 5) transita la casa 3
    asp = transits.calculate_transit_aspects(_natal(), [_P("saturn", 165.0)], include_angles=True)
    assert all(a["transit_house"] == 3 for a in asp)


def test_sin_hora_no_hay_angulos_ni_casas_de_transito():
    asp = transits.calculate_transit_aspects(_natal(time_known=False), [_P("pluto", 101.0)],
                                             include_angles=True)
    assert not any(a["natal_planet"] == "ascendant" for a in asp)
    assert all(a.get("transit_house") is None for a in asp)


# ── Lo que ve el modelo ──────────────────────────────────────────────────────

def _carta(time_known=True):
    return {
        "time_known": time_known,
        "planets": [
            {"name": "sun", "sign": "Leo", "degree_in_sign": 3.0, "house": 11, "longitude": 123.0,
             "is_retrograde": False},
            {"name": "saturn", "sign": "Piscis", "degree_in_sign": 8.0, "house": 6, "longitude": 338.0,
             "is_retrograde": True},
        ],
        "ascendant": {"sign": "Virgo", "degree": 4.18, "absolute_longitude": 154.18},
        "midheaven": {"sign": "Tauro", "degree": 29.64, "absolute_longitude": 59.64},
        "aspects": [],
    }


def _contexto(monkeypatch, carta, significativos=()):
    monkeypatch.setattr(astrology, "get_birth_chart_for_user", lambda *a, **k: carta)
    monkeypatch.setattr(astrology, "get_transits_for_user",
                        lambda *a, **k: {"most_significant": list(significativos)})
    return astrology.format_astrology_context("U1")


def test_el_contexto_incluye_los_cuatro_angulos_y_los_retrogrados(monkeypatch):
    texto = _contexto(monkeypatch, _carta())
    for nombre in ("Ascendente: Virgo", "Descendente: Piscis", "Medio Cielo: Tauro",
                   "Fondo del Cielo: Escorpio"):
        assert nombre in texto
    assert "Saturno: Piscis 8.0° ℞" in texto
    assert "SIN HORA" not in texto


def test_el_contexto_muestra_la_casa_de_transito_y_el_retrogrado(monkeypatch):
    t = {"transit_planet": "pluto", "transit_sign": "Acuario", "transit_house": 6,
         "transit_retrograde": True, "natal_planet": "ascendant", "natal_sign": "Virgo",
         "natal_house": 1, "aspect_type": "cuadratura", "orb": 1.2, "nivel": "fondo",
         "relevancia": 50}
    texto = _contexto(monkeypatch, _carta(), [t])
    assert "Plutón en tránsito (Acuario, por tu casa 6) ℞" in texto
    assert "con tu Ascendente natal (Virgo, casa 1)" in texto


def test_sin_hora_el_contexto_no_inventa_ascendente_ni_casas(monkeypatch):
    texto = _contexto(monkeypatch, _carta(time_known=False))
    assert "SIN HORA DE NACIMIENTO" in texto
    assert "Ascendente: Virgo" not in texto and "Medio Cielo: Tauro" not in texto
    assert ", casa " not in texto


def test_los_aspectos_natales_no_repiten_el_nodo_sur(monkeypatch):
    carta = _carta()
    carta["aspects"] = [
        {"planet1": "moon", "planet2": "north_node", "aspect_type": "conjunción", "orb": 3.0, "exact_angle": 0},
        {"planet1": "moon", "planet2": "south_node", "aspect_type": "oposición", "orb": 3.0, "exact_angle": 180},
    ]
    texto = _contexto(monkeypatch, carta)
    assert "Luna conjunción Nodo Norte" in texto
    assert "Nodo Sur" not in texto.split("## ASPECTOS NATALES")[1].split("##")[0]
