"""
Jerarquía de la lectura astrológica: lo lento pesa más que lo rápido.
"""
import astrology
from astro_core import relevancia as rel
from astro_core import transits


def _t(planeta, natal, aspecto="conjunción", orbe=1.0, orbe_max=6.0):
    return rel.puntuar_transito(planeta, natal, aspecto, orbe, orbe_max)


def test_pluton_sobre_el_sol_pesa_mas_que_una_luna_exacta():
    # Aunque la Luna esté casi exacta y Plutón a 2°, Plutón manda.
    assert _t("pluto", "sun", orbe=2.0) > _t("moon", "sun", orbe=0.1) * 10


def test_la_luna_nunca_supera_a_un_lento_sobre_un_planeta_personal():
    for lento in ("pluto", "neptune", "uranus", "saturn"):
        assert _t(lento, "venus", "cuadratura", orbe=3.5) > _t("moon", "sun", orbe=0.0)


def test_mas_exacto_pesa_mas_a_igualdad_de_todo_lo_demas():
    assert _t("saturn", "moon", orbe=0.5) > _t("saturn", "moon", orbe=3.0)


def test_los_luminares_pesan_mas_que_los_transpersonales_natales():
    assert _t("saturn", "sun") > _t("saturn", "neptune")


def test_niveles():
    assert rel.nivel_transito("pluto") == "fondo"
    assert rel.nivel_transito("jupiter") == "apoyo"
    assert rel.nivel_transito("moon") == "disparador"
    assert rel.nivel_transito("mars") == "disparador"


def test_los_aspectos_de_los_transitos_llevan_nivel_y_relevancia_y_se_ordenan(monkeypatch):
    class P:
        def __init__(self, name, lon):
            self.name, self.longitude, self.sign, self.house = name, lon, "Aries", 1

        def to_dict(self):
            return {"name": self.name, "longitude": self.longitude}

    natal = type("C", (), {"planets": [P("sun", 100.0)], "latitude": 0, "longitude": 0})()
    en_transito = [P("moon", 100.1), P("pluto", 102.0)]   # la Luna está más exacta
    aspectos = transits.calculate_transit_aspects(natal, en_transito)
    assert {a["transit_planet"] for a in aspectos} == {"moon", "pluto"}
    assert all("nivel" in a and "relevancia" in a for a in aspectos)

    monkeypatch.setattr(transits, "get_planet_positions_at", lambda *a, **k: en_transito)
    informe = transits.get_transits_report_for_date(natal)
    assert informe["most_significant"][0]["transit_planet"] == "pluto"


# ── Lo que ve el modelo ──────────────────────────────────────────────────────

def _contexto(monkeypatch, significativos):
    carta = {
        "planets": [{"name": "sun", "sign": "Leo", "degree_in_sign": 10, "house": 5}],
        "ascendant": {"sign": "Tauro", "degree": 3},
        "aspects": [
            {"planet1": "sun", "planet2": "moon", "aspect_type": "trígono", "orb": 2.0, "exact_angle": 120},
            {"planet1": "neptune", "planet2": "pluto", "aspect_type": "sextil", "orb": 1.0, "exact_angle": 60},
        ],
    }
    monkeypatch.setattr(astrology, "get_birth_chart_for_user", lambda *a, **k: carta)
    monkeypatch.setattr(astrology, "get_transits_for_user",
                        lambda *a, **k: {"most_significant": significativos})
    return astrology.format_astrology_context("U1")


def _asp(planeta, nivel, orbe):
    return {"transit_planet": planeta, "transit_sign": "Piscis", "natal_planet": "sun",
            "natal_sign": "Leo", "natal_house": 5, "aspect_type": "cuadratura",
            "orb": orbe, "nivel": nivel, "relevancia": 1}


def test_el_contexto_agrupa_los_transitos_por_peso(monkeypatch):
    texto = _contexto(monkeypatch, [_asp("moon", "disparador", 0.1), _asp("pluto", "fondo", 2.0)])
    assert texto.index("Telón de fondo") < texto.index("Plutón en tránsito")
    assert texto.index("Plutón en tránsito") < texto.index("Disparadores")
    assert texto.index("Disparadores") < texto.index("Luna en tránsito")


def test_el_contexto_lleva_el_metodo_de_lectura_y_los_aspectos_natales(monkeypatch):
    texto = _contexto(monkeypatch, [_asp("pluto", "fondo", 2.0)])
    assert "CÓMO ORDENAR LA LECTURA" in texto
    assert "NUNCA se presentan como lo principal" in texto
    assert "ASPECTOS NATALES PRINCIPALES" in texto
    # Sol-Luna pesa más que Neptuno-Plutón y va primero
    assert texto.index("Sol trígono Luna") < texto.index("Neptuno sextil Plutón")


def test_sin_aspectos_natales_ni_transitos_no_falla(monkeypatch):
    texto = _contexto(monkeypatch, [])
    assert "CÓMO ORDENAR LA LECTURA" in texto
    assert "ASPECTOS NATALES PRINCIPALES" in texto   # los de la carta de prueba
