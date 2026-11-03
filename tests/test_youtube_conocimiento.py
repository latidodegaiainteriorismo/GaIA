"""
Ingesta de YouTube (scripts/ingest_youtube.py) y visibilidad privada del conocimiento.
"""
import importlib.util
import os
import re

import pytest

import knowledge
import query_router

_RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("ingest_youtube", os.path.join(_RAIZ, "scripts", "ingest_youtube.py"))
yt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(yt)


# ── Filtro de títulos (casos reales del canal de José Millán) ────────────────

@pytest.mark.parametrize("titulo", [
    "El Sol en la octava casa. La luz que brilla en las tinieblas.",
    "Saturno en tránsito en cuadratura con la Luna natal.",
    "Curso aspectos entre planetas",
    "Deconstruyendo Tauro",
    "Venus con Juan Carlos Gómez",
    "El yod. El dedo de Dios.",
    "Las cualidades astrológicas:  Caliente-Frío, Húmedo-Seco.",
    "LIBRA, el otro como espejo. Parte I. Curso introducción a la astrología Humanística",
    "Plutón en aspecto con Marte natal.",
    # método y jerarquía de lectura
    "Tránsitos.  Guía para navegantes (primera parte).",
    "Los tres niveles de expresión en la interpretación de un tránsito o progresión.",
    "Presentación del curso de síntesis interpretativa de cartas natales.",
])
def test_incluye_contenido_practico(titulo):
    assert yt.clasificar(titulo) == "incluir"


def test_priorizar_pone_primero_el_metodo_de_lectura():
    metodo = yt.prioridad("Tránsitos.  Guía para navegantes (primera parte).")
    casas = yt.prioridad("Planetas en la casa VI con Juan Carlos Gómez")
    assert metodo < casas < yt.prioridad("Un vídeo cualquiera")


@pytest.mark.parametrize("titulo", [
    # periódicos aunque el título pegadizo hable de "carta natal" o "casa"
    "Tu carta natal no te determina, te revela  La astrología de la primera mitad de febrero y la Luna llena",
    "El eterno regreso a casa. La astrología de la segunda mitad de julio y la luna llena en Acuario",
    "Leo. Pronóstico julio 2017.",
    "Luna nueva en Piscis el 23 de febrero. REALISMO MÁGICO",
    "Yo soy la muerte, el destructor de mundos. La astrología del ingreso de Plutón en Acuario",
    # eventos concretos y teoría de eras
    "La conjunción Marte-Saturno de Abril del 2018.  La violencia del sistema.",
    "Las eras astrológicas: la era de Aries. Capítulo 5 de la serie \"eras astrológicas\"",
    # actualidad y entrevistas
    "Trump con May de Chiara",
    "Carles Puigdemont. La exaltación de la subjetividad.",
    "Astrología para después del Apocalipsis",
])
def test_descarta_periodicos_y_actualidad(titulo):
    assert yt.clasificar(titulo) != "incluir"


# ── Texto ────────────────────────────────────────────────────────────────────

def test_limpia_marcas_de_los_subtitulos():
    assert yt.limpiar_transcripcion(["[Música]", " hola  mundo ", "", "[Aplausos] adiós"]) == "hola mundo adiós"


def test_trocear_respeta_el_tamano_y_solapa():
    texto = " ".join(f"p{i}" for i in range(600))
    chunks = yt.trocear(texto)
    assert all(len(c.split()) <= yt.CHUNK_WORDS for c in chunks)
    assert chunks[0].split()[-yt.OVERLAP_WORDS:] == chunks[1].split()[:yt.OVERLAP_WORDS]
    assert chunks[-1].split()[-1] == "p599"          # no se pierde el final
    assert yt.trocear("") == []


def test_cada_chunk_lleva_el_titulo():
    chunks = yt.preparar_chunks("Saturno en la casa 10", "uno dos tres")
    assert chunks == ["Saturno en la casa 10. uno dos tres"]


def test_la_etiqueta_de_fuente_la_encuentra_el_router():
    rx = re.compile("^" + "".join(".*" if c == "%" else "." if c == "_" else re.escape(c)
                                  for c in knowledge.patron_fuente("José Millán")) + "$", re.I | re.S)
    assert rx.match(yt.etiqueta_fuente("El Sol en la tercera casa", yt.AUTOR_POR_DEFECTO))
    assert "José Millán" in query_router.DOCUMENT_CATALOG


# ── Visibilidad: lo privado solo lo ve el creator ────────────────────────────

@pytest.fixture
def busqueda(monkeypatch):
    rec = {"visibilidades": [], "limites": []}
    monkeypatch.setattr(knowledge, "_search_monica", lambda m: [])
    monkeypatch.setattr(knowledge, "preprocess_query",
                        lambda m: {"documents": ["José Millán"], "keywords": ["saturno"], "needs_web": False})

    def fts(terms, source_filter=None, limit=3, visibilities=("all",)):
        rec["visibilidades"].append(tuple(visibilities))
        rec["limites"].append(limit)
        return [{"source": "x (José Millán - YouTube)", "content": "c", "category": "Astrología"}]

    monkeypatch.setattr(knowledge, "_fts_search", fts)
    monkeypatch.setattr(knowledge, "search_web", lambda q: "")
    monkeypatch.setattr(knowledge, "search_audio_chunks", lambda *a, **k: [])
    return rec


def test_usuario_normal_no_ve_lo_privado(busqueda):
    knowledge.search_knowledge("saturno en casa 10")
    assert busqueda["visibilidades"] == [("all",)]


def test_el_creator_ve_lo_privado_y_se_le_dan_mas_fragmentos(busqueda):
    knowledge.search_knowledge_creator("saturno en casa 10", "U1")
    assert ("creator", "all") in busqueda["visibilidades"]
    assert busqueda["limites"][0] == 4          # José Millán: TOP_K_POR_DOCUMENTO_AMPLIO
