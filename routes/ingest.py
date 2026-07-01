import re
import base64
import logging
import requests as req
from io import BytesIO
from flask import Blueprint, request, jsonify
from db import db_run, db_one
from config import HUGGINGFACE_API_KEY, HUGGINGFACE_MODEL, EMBEDDING_DIMENSIONS, KNOWLEDGE_TOP_K

logger    = logging.getLogger(__name__)
ingest_bp = Blueprint('ingest', __name__)

# ── Chunking ──────────────────────────────────────────────────────────────────
def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    """
    Divide texto en chunks de ~chunk_size tokens con solapamiento.
    Respeta párrafos: no corta a mitad de párrafo si puede evitarlo.
    """
    # Limpiar texto
    text = re.sub(r'\n{3,}', '\n\n', text.strip())
    text = re.sub(r'[ \t]+', ' ', text)

    # Estimar tokens (palabras / 0.75)
    words    = text.split()
    chunks   = []
    start    = 0
    size_w   = int(chunk_size * 0.75)
    overlap_w = int(overlap * 0.75)

    while start < len(words):
        end   = min(start + size_w, len(words))
        chunk = ' '.join(words[start:end])
        if chunk.strip():
            chunks.append(chunk.strip())
        start += size_w - overlap_w

    return chunks

# ── Embeddings ────────────────────────────────────────────────────────────────
def get_embedding(text: str) -> list[float] | None:
    """Genera embedding de 384 dimensiones via HuggingFace Inference API."""
    if not HUGGINGFACE_API_KEY:
        logger.error('[Ingest] HUGGINGFACE_API_KEY no configurada')
        return None
    try:
        resp = req.post(
            f'https://api-inference.huggingface.co/pipeline/feature-extraction/{HUGGINGFACE_MODEL}',
            headers={'Authorization': f'Bearer {HUGGINGFACE_API_KEY}'},
            json={'inputs': text, 'options': {'wait_for_model': True}},
            timeout=30
        )
        if resp.status_code == 200:
            result = resp.json()
            # HuggingFace devuelve [[...]] para feature-extraction
            if isinstance(result, list) and isinstance(result[0], list):
                # Si devuelve matriz (token embeddings), hacer mean pooling
                if isinstance(result[0][0], list):
                    import statistics
                    return [statistics.mean(row[i] for row in result[0]) for i in range(len(result[0][0]))]
                return result[0]
            return result
        logger.error(f'[Ingest] HuggingFace error {resp.status_code}: {resp.text[:200]}')
        return None
    except Exception as e:
        logger.error(f'[Ingest] Embedding error: {e}')
        return None

# ── Extractores de texto ──────────────────────────────────────────────────────
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
        logger.error(f'[Ingest] PDF extraction error: {e}')
        raise ValueError(f'Error extrayendo PDF: {str(e)}')

def extract_youtube(url: str) -> str:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        # Extraer video ID de la URL
        import re
        match = re.search(r'(?:v=|youtu\.be/)([a-zA-Z0-9_-]{11})', url)
        if not match:
            raise ValueError('URL de YouTube no válida')
        video_id = match.group(1)
        transcript = YouTubeTranscriptApi.get_transcript(video_id, languages=['es', 'en'])
        text = ' '.join(entry['text'] for entry in transcript)
        return text.strip()
    except Exception as e:
        logger.error(f'[Ingest] YouTube extraction error: {e}')
        raise ValueError(f'Error extrayendo transcripción: {str(e)}')

# ── Endpoint /ingest ───────────────────────────────────────────────────────────
@ingest_bp.route('/ingest', methods=['POST'])
def ingest():
    data = request.get_json() or {}

    source_type = data.get('type')        # 'pdf' | 'text' | 'youtube'
    title       = (data.get('title') or '').strip()
    description = (data.get('description') or '').strip()
    category    = (data.get('category') or 'otro').strip()
    content     = data.get('content', '')

    if not source_type or not title or not description or not content:
        return jsonify({'error': 'Faltan campos obligatorios: type, title, description, content'}), 400

    logger.info(f'[Ingest] type={source_type} title="{title}" category={category}')

    # ── 1. Extraer texto según tipo ───────────────────────────────────────────
    try:
        if source_type == 'pdf':
            text = extract_pdf(content)
        elif source_type == 'text':
            text = content.strip()
        elif source_type == 'youtube':
            text = extract_youtube(content)
        else:
            return jsonify({'error': f'Tipo no soportado: {source_type}'}), 400
    except ValueError as e:
        return jsonify({'error': str(e)}), 422

    if not text:
        return jsonify({'error': 'No se pudo extraer texto del contenido'}), 422

    logger.info(f'[Ingest] Texto extraído: {len(text)} chars')

    # ── 2. Chunking ───────────────────────────────────────────────────────────
    chunks = chunk_text(text)
    logger.info(f'[Ingest] {len(chunks)} chunks generados')

    if not chunks:
        return jsonify({'error': 'El texto no produjo chunks válidos'}), 422

    # ── 3. Embedding + guardado en Supabase ───────────────────────────────────
    saved  = 0
    errors = 0
    source_label = f'{title} ({source_type})'

    for i, chunk in enumerate(chunks):
        embedding = get_embedding(chunk)
        if not embedding:
            logger.warning(f'[Ingest] Chunk {i+1}/{len(chunks)} sin embedding — saltando')
            errors += 1
            continue

        ok = db_run(
            """
            INSERT INTO knowledge_chunks (source, category, content, embedding)
            VALUES (%s, %s, %s, %s::vector)
            """,
            (source_label, category, chunk, str(embedding))
        )
        if ok:
            saved += 1
        else:
            errors += 1

        if (i + 1) % 10 == 0:
            logger.info(f'[Ingest] Progreso: {i+1}/{len(chunks)} chunks')

    logger.info(f'[Ingest] ✅ Completado: {saved} guardados, {errors} errores')

    return jsonify({
        'status':         'ok',
        'chunks_created': saved,
        'chunks_total':   len(chunks),
        'errors':         errors,
        'chars_extracted': len(text),
        'title':          title,
        'category':       category,
    })
