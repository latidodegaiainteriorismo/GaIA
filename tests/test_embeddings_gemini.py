"""
Embeddings con Gemini (nov-2026): petición correcta, 384 dimensiones normalizadas,
documento/consulta distinguidos, lote alineado con la entrada y pausa tras 429.
"""
import math

import embeddings


class _Resp:
    def __init__(self, status=200, cuerpo=None):
        self.status_code, self._cuerpo, self.text = status, cuerpo or {}, str(cuerpo)

    def json(self):
        return self._cuerpo


def _vec(valor=0.3, n=None):
    return [valor] * (n or embeddings.EMBEDDING_DIMENSIONS)


def _preparar(monkeypatch, respuesta):
    llamadas = []

    def falso(url, headers=None, json=None, timeout=None):
        llamadas.append({"url": url, "headers": headers, "json": json})
        return respuesta(json) if callable(respuesta) else respuesta

    monkeypatch.setattr(embeddings, "GEMINI_API_KEY", "clave-de-prueba")
    monkeypatch.setattr(embeddings, "_pausado_hasta", 0.0)
    monkeypatch.setattr(embeddings.requests, "post", falso)
    return llamadas


def test_embed_text_pide_384_dimensiones_y_devuelve_el_vector_normalizado(monkeypatch):
    llamadas = _preparar(monkeypatch, _Resp(200, {"embedding": {"values": _vec(0.3)}}))
    vec = embeddings.embed_text("Saturno en la casa diez")

    assert len(vec) == 384
    assert math.isclose(math.sqrt(sum(x * x for x in vec)), 1.0, rel_tol=1e-9)
    pet = llamadas[0]
    assert pet["url"].endswith("gemini-embedding-001:embedContent")
    assert pet["headers"] == {"x-goog-api-key": "clave-de-prueba"}      # la clave va en cabecera, no en la URL
    assert "clave-de-prueba" not in pet["url"]
    assert pet["json"]["outputDimensionality"] == 384
    assert pet["json"]["taskType"] == "RETRIEVAL_DOCUMENT"


def test_la_busqueda_usa_tarea_de_consulta(monkeypatch):
    llamadas = _preparar(monkeypatch, _Resp(200, {"embedding": {"values": _vec()}}))
    embeddings.embed_text("¿qué es Saturno?", consulta=True)
    assert llamadas[0]["json"]["taskType"] == "RETRIEVAL_QUERY"


def test_dimension_inesperada_devuelve_none(monkeypatch):
    _preparar(monkeypatch, _Resp(200, {"embedding": {"values": _vec(n=768)}}))
    assert embeddings.embed_text("hola mundo") is None


def test_texto_vacio_o_sin_clave_no_llama(monkeypatch):
    llamadas = _preparar(monkeypatch, _Resp(200, {"embedding": {"values": _vec()}}))
    assert embeddings.embed_text("   ") is None
    monkeypatch.setattr(embeddings, "GEMINI_API_KEY", "")
    assert embeddings.embed_text("hola") is None
    assert llamadas == []


def test_texto_muy_largo_se_recorta(monkeypatch):
    llamadas = _preparar(monkeypatch, _Resp(200, {"embedding": {"values": _vec()}}))
    embeddings.embed_text("a" * 20000)
    enviado = llamadas[0]["json"]["content"]["parts"][0]["text"]
    assert len(enviado) == embeddings._MAX_CHARS


def test_un_429_pausa_un_minuto_y_no_media_hora(monkeypatch):
    llamadas = _preparar(monkeypatch, _Resp(429))
    assert embeddings.embed_text("uno") is None
    assert embeddings.embed_text("dos") is None
    assert len(llamadas) == 1
    restante = embeddings._pausado_hasta - __import__("time").monotonic()
    assert 0 < restante <= embeddings._PAUSA_TRAS_LIMITE_S


def test_lote_mantiene_la_posicion_de_cada_texto(monkeypatch):
    def responder(json):
        n = len(json["requests"])
        return _Resp(200, {"embeddings": [{"values": _vec(0.1 * (k + 1))} for k in range(n)]})

    llamadas = _preparar(monkeypatch, responder)
    res = embeddings.embed_textos(["uno", "", "tres"])

    assert len(llamadas) == 1 and len(llamadas[0]["json"]["requests"]) == 2   # el vacío no se envía
    assert res[1] is None
    assert res[0] is not None and res[2] is not None
    assert llamadas[0]["url"].endswith(":batchEmbedContents")


def test_lote_con_error_devuelve_todo_none(monkeypatch):
    _preparar(monkeypatch, _Resp(500))
    assert embeddings.embed_textos(["uno", "dos"]) == [None, None]
