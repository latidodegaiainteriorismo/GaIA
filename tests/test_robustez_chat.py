"""
Robustez del chat: la voz no puede tumbar la petición, los embeddings sin créditos no
hacen perder tiempo y la expansión de temas de memoria usa un parámetro válido.
Casos reales del 3-nov-2026 (registro de Render): WORKER TIMEOUT sintetizando 4827
caracteres, 25 llamadas a HuggingFace con HTTP 402 por consolidación y HTTP 400 de
Gemini por reasoning_effort="none".
"""
import asyncio
import time

import embeddings
import llm
import memory_search
import tts


# ── Voz ──────────────────────────────────────────────────────────────────────

def test_la_voz_que_tarda_demasiado_devuelve_none_sin_bloquear(monkeypatch):
    async def lenta(texto):
        await asyncio.sleep(5)
        return b"audio"

    monkeypatch.setattr(tts, "_generate", lenta)
    t0 = time.monotonic()
    assert tts.text_to_speech("Hola, ¿qué tal?", timeout=0.05) is None
    assert time.monotonic() - t0 < 2


def test_la_voz_corta_el_texto_largo_pero_no_el_texto_mostrado(monkeypatch):
    recibido = {}

    async def falsa(texto):
        recibido["texto"] = texto
        return b"audio"

    monkeypatch.setattr(tts, "_generate", falsa)
    largo = "Esta es una frase de prueba para la voz. " * 120      # ~4900 caracteres
    assert tts.text_to_speech(largo) is not None
    assert len(recibido["texto"]) <= tts._TTS_MAX_CHARS
    assert recibido["texto"].endswith(".")                          # corta en frase completa


def test_el_limite_de_voz_cabe_en_el_tiempo_del_worker():
    # ~7 ms por carácter medidos en Render: el tope debe sintetizarse muy por debajo de 30 s
    assert tts._TTS_MAX_CHARS * 0.007 < 20


# ── Embeddings sin créditos ──────────────────────────────────────────────────

class _Resp:
    def __init__(self, status):
        self.status_code, self.text = status, '{"error":"no credits"}'


def test_tras_un_402_no_se_vuelve_a_llamar_a_huggingface_un_rato(monkeypatch):
    llamadas = []
    monkeypatch.setattr(embeddings, "HUGGINGFACE_API_KEY", "x")
    monkeypatch.setattr(embeddings, "_pausado_hasta", 0.0)
    monkeypatch.setattr(embeddings.requests, "post", lambda *a, **k: llamadas.append(1) or _Resp(402))

    assert embeddings.embed_text("uno") is None
    assert embeddings.embed_text("dos") is None
    assert embeddings.embed_text("tres") is None
    assert len(llamadas) == 1                       # antes: una llamada fallida por cada mensaje


def test_pasada_la_pausa_se_reintenta(monkeypatch):
    llamadas = []
    monkeypatch.setattr(embeddings, "HUGGINGFACE_API_KEY", "x")
    monkeypatch.setattr(embeddings, "_pausado_hasta", time.monotonic() - 1)
    monkeypatch.setattr(embeddings.requests, "post", lambda *a, **k: llamadas.append(1) or _Resp(402))
    embeddings.embed_text("uno")
    assert len(llamadas) == 1


def test_un_error_pasajero_no_pausa_los_embeddings(monkeypatch):
    llamadas = []
    monkeypatch.setattr(embeddings, "HUGGINGFACE_API_KEY", "x")
    monkeypatch.setattr(embeddings, "_pausado_hasta", 0.0)
    monkeypatch.setattr(embeddings.requests, "post", lambda *a, **k: llamadas.append(1) or _Resp(503))
    embeddings.embed_text("uno")
    embeddings.embed_text("dos")
    assert len(llamadas) == 2


# ── Expansión de temas de memoria ────────────────────────────────────────────

def test_la_expansion_de_temas_no_pide_reasoning_effort_none(monkeypatch):
    capturado = {}

    class _Completions:
        def create(self, **kwargs):
            capturado.update(kwargs)
            raise RuntimeError("corta aquí: solo interesan los parámetros")

    class _Cliente:
        def with_options(self, **k):
            return self
        chat = type("C", (), {"completions": _Completions()})()

    monkeypatch.setattr(llm, "_client", _Cliente())
    assert memory_search._expand_memory_themes("hola") == []
    assert capturado["reasoning_effort"] != "none"       # Gemini lo rechaza con HTTP 400
