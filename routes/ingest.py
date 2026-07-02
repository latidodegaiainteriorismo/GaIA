import re
import base64
import logging
from io import BytesIO
from flask import Blueprint, request, jsonify
from db import db_run

logger    = logging.getLogger(__name__)
ingest_bp = Blueprint('ingest', __name__)

# ── Modelo de embeddings (carga lazy, una sola vez) ───────────────────────────
_embed_model = None

def get_embed_model():
    global _embed_model
    if _embed_model is None:
        from fastembed import TextEmbedding
        logger.info('[Ingest] Cargando modelo de embeddings...')
        _embed_model = TextEmbedding(
            model_name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
            cache_dir="/tmp/fastembed_cache"
        )
        logger.info('[Ingest] Modelo cargado ✅')
    return _embed_model

def get_embedding(text: str) -> list[float] | None:
    try:
        model  = get_embed_model()
        result = list(model.embed([text]))
        return result[0].tolist()
    except Exception as e:
        logger.error(f'[Ingest] Embedding error: {e}')
        return None

# ── Diagnóstico: qué modelos están disponibles ────────────────────────────────
@ingest_bp.route('/ingest/models', methods=['GET'])
def list_models():
    try:
        from fastembed import TextEmbedding
        models = TextEmbedding.list_supported_models()
        return jsonify({'models': [m['model'] for m in models]})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ── Chunking ──────────────────────────────────────────────────────────────────
def chunk_text(text: str, chunk_size: int = 300, overlap: int = 30) -> list[str]:
    text      = re.sub(r'\n{3,}', '\n\n', text.strip())
    text      = re.sub(r'[ \t]+', ' ', text)
    words     = text.split()
    chunks    = []
    size_w    = int(chunk_size * 0.75)
    overlap_w = int(overlap   * 0.75)
    start     = 0
    while start < len(words):
        end   = min(start + size_w, len(words))
        chunk = ' '.join(words[start:end])
        if chunk.strip():
            chunks.append(chunk.strip())
        start += size_w - overlap_w
    return chunks

# ── Extractores ───────────────────────────────────────────────────────────────
def extract_pdf(base64_content: str) -> str:
    try:
        import pdfplumber
        pdf_bytes = base64.b64decode(base64_content)
        text = ''
        with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + '\n\n'
        return text.strip()
    except Exception as e:
        raise ValueError(f'Error extrayendo PDF: {e}')

def extract_youtube(url: str) -> str:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        match = re.search(r'(?:v=|youtu\.be/)([a-zA-Z0-9_-]{11})', url)
        if not match:
            raise ValueError('URL de YouTube no válida')
        video_id   = match.group(1)
        transcript = YouTubeTranscriptApi.get_transcript(video_id, languages=['es', 'en'])
        return ' '.join(e['text'] for e in transcript).strip()
    except Exception as e:
        raise ValueError(f'Error extrayendo transcripción: {e}')

# ── Endpoint /ingest ───────────────────────────────────────────────────────────
@ingest_bp.route('/ingest', methods=['POST'])
def ingest():
    data = request.get_json() or {}

    source_type = data.get('type')
    title       = (data.get('title')       or '').strip()
    description = (data.get('description') or '').strip()
    category    = (data.get('category')    or 'otro').strip()
    content     = data.get('content', '')

    if not source_type or not title or not description or not content:
        return jsonify({'error': 'Faltan campos: type, title, description, content'}), 400

    logger.info(f'[Ingest] type={source_type} title="{title}" category={category}')

    # 1. Extraer texto
    try:
        if   source_type == 'pdf':     text = extract_pdf(content)
        elif source_type == 'text':    text = content.strip()
        elif source_type == 'youtube': text = extract_youtube(content)
        else: return jsonify({'error': f'Tipo no soportado: {source_type}'}), 400
    except ValueError as e:
        return jsonify({'error': str(e)}), 422

    if not text:
        return jsonify({'error': 'No se pudo extraer texto'}), 422

    logger.info(f'[Ingest] Texto extraído: {len(text)} chars')

    # 2. Chunking
    chunks = chunk_text(text)
    logger.info(f'[Ingest] {len(chunks)} chunks generados')
    if not chunks:
        return jsonify({'error': 'El texto no produjo chunks'}), 422

    # 3. Embeddings + guardado
    source_label = f'{title} ({source_type})'
    saved = errors = 0

    for i, chunk in enumerate(chunks):
        embedding = get_embedding(chunk)
        if not embedding:
            errors += 1
            continue

        ok = db_run(
            """
            INSERT INTO knowledge_chunks (source, category, content, embedding)
            VALUES (%s, %s, %s, %s::vector)
            """,
            (source_label, category, chunk, str(embedding))
        )
        if ok: saved  += 1
        else:  errors += 1

        if (i + 1) % 10 == 0:
            logger.info(f'[Ingest] Progreso: {i+1}/{len(chunks)}')

    logger.info(f'[Ingest] ✅ Completado: {saved} guardados, {errors} errores')

    return jsonify({
        'status':          'ok',
        'chunks_created':  saved,
        'chunks_total':    len(chunks),
        'errors':          errors,
        'chars_extracted': len(text),
        'title':           title,
        'category':        category,
    })
