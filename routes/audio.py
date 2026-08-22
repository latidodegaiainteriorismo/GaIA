"""
routes/audio.py

Blueprint de gestión de audios del creator de GaIA.

Endpoints:
  POST   /audio/upload          Sube un audio, lo transcribe con Whisper,
                                genera chunks con timestamps, embeddings,
                                genera título automático con Gemini,
                                y guarda el binario en Supabase Storage.
  GET    /audio/list            Lista los audios del creator con metadatos.
  PATCH  /audio/<id>/title      Actualiza el título de un audio.
  PATCH  /audio/<id>/promote    Promueve un audio a visibilidad 'all'
                                (base de conocimiento general).
  DELETE /audio/<id>            Elimina un audio y todos sus chunks.

Permisos: todos los endpoints requieren rol 'creator' (ver auth.py).
"""

import io
import re
import logging
import requests as http_requests
from flask import Blueprint, request, jsonify, g
from groq import Groq
from db import db_one, db_all, db_run
from auth import require_creator
from embeddings import embed_text
from config import (
    GROQ_API_KEY, WHISPER_MODEL, AUDIO_CHUNK_WORDS,
    SUPABASE_URL, SUPABASE_SERVICE_KEY, SUPABASE_AUDIO_BUCKET,
    GEMINI_API_KEY, GEMINI_BASE_URL, GEMINI_MODEL_GENERAL,
)
from openai import OpenAI

logger    = logging.getLogger(__name__)
audio_bp  = Blueprint('audio', __name__)

_groq     = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None
_gemini   = OpenAI(api_key=GEMINI_API_KEY, base_url=GEMINI_BASE_URL) if GEMINI_API_KEY else None


# ── Helpers de Supabase Storage ───────────────────────────────────────────────

def _storage_headers() -> dict:
    return {
        'Authorization': f'Bearer {SUPABASE_SERVICE_KEY}',
        'Content-Type':  'application/octet-stream',
    }


def _upload_to_storage(path: str, data: bytes, content_type: str = 'audio/webm') -> bool:
    """Sube un archivo al bucket gaia-audios. Devuelve True si tuvo éxito."""
    url = f'{SUPABASE_URL}/storage/v1/object/{SUPABASE_AUDIO_BUCKET}/{path}'
    headers = {
        'Authorization': f'Bearer {SUPABASE_SERVICE_KEY}',
        'Content-Type':  content_type,
        'x-upsert':      'true',   # sobreescribe si ya existe (resubida)
    }
    try:
        r = http_requests.put(url, data=data, headers=headers, timeout=60)
        if r.status_code in (200, 201):
            return True
        logger.error(f'[Audio Storage] Error {r.status_code}: {r.text[:200]}')
        return False
    except Exception as e:
        logger.error(f'[Audio Storage] Excepción al subir: {e}')
        return False


def _delete_from_storage(path: str) -> bool:
    """Elimina un archivo del bucket. Devuelve True si tuvo éxito."""
    url = f'{SUPABASE_URL}/storage/v1/object/{SUPABASE_AUDIO_BUCKET}/{path}'
    try:
        r = http_requests.delete(url, headers=_storage_headers(), timeout=15)
        return r.status_code in (200, 204)
    except Exception as e:
        logger.error(f'[Audio Storage] Excepción al eliminar: {e}')
        return False


# ── Transcripción con Whisper ──────────────────────────────────────────────────

def _transcribe(audio_bytes: bytes, filename: str) -> dict | None:
    """
    Transcribe el audio con Groq Whisper Large v3.
    Devuelve el objeto de respuesta verbose_json con segmentos y timestamps,
    o None si falla.

    verbose_json incluye:
      - .text: transcripción completa
      - .segments: lista de {id, start, end, text} por segmento
    """
    if not _groq:
        logger.error('[Audio] Cliente Groq no inicializado — revisa GROQ_API_KEY')
        return None

    # Detectar content-type por extensión
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else 'webm'
    mime_map = {
        'webm': 'audio/webm', 'mp3': 'audio/mpeg', 'mp4': 'audio/mp4',
        'wav':  'audio/wav',  'ogg': 'audio/ogg',  'm4a': 'audio/mp4',
    }
    mime = mime_map.get(ext, 'audio/webm')

    try:
        logger.info(f'[Audio] Transcribiendo {filename} ({len(audio_bytes)} bytes) con Whisper')
        response = _groq.audio.transcriptions.create(
            file=(filename, io.BytesIO(audio_bytes), mime),
            model=WHISPER_MODEL,
            response_format='verbose_json',
            timestamp_granularities=['segment'],
            language='es',
        )
        logger.info(f'[Audio] Transcripción OK: {len(response.text)} chars, '
                    f'{len(response.segments)} segmentos')
        return response
    except Exception as e:
        logger.error(f'[Audio] Error Whisper: {e}')
        return None


# ── Chunking del transcript ────────────────────────────────────────────────────

def _chunk_transcript(segments: list, words_per_chunk: int = AUDIO_CHUNK_WORDS) -> list[dict]:
    """
    Agrupa los segmentos de Whisper en chunks de ~words_per_chunk palabras,
    respetando los límites entre segmentos para no cortar frases a mitad.

    Cada chunk tiene:
      - content:    texto del chunk
      - start_time: segundo de inicio del primer segmento incluido
      - end_time:   segundo de fin del último segmento incluido

    Whisper devuelve segmentos de ~5-15 palabras, así que un chunk de 150
    palabras equivale a ~45-60 segundos de audio.
    """
    chunks   = []
    current  = []
    words    = 0
    t_start  = 0.0

    for seg in segments:
        seg_words = len(seg.text.split())
        if not current:
            t_start = seg.start
        current.append(seg)
        words += seg_words

        if words >= words_per_chunk:
            chunks.append({
                'content':    ' '.join(s.text.strip() for s in current),
                'start_time': t_start,
                'end_time':   current[-1].end,
            })
            current = []
            words   = 0

    # Último chunk parcial (si quedaron segmentos sin cerrar)
    if current:
        chunks.append({
            'content':    ' '.join(s.text.strip() for s in current),
            'start_time': t_start,
            'end_time':   current[-1].end,
        })

    logger.info(f'[Audio] {len(segments)} segmentos → {len(chunks)} chunks')
    return chunks


# ── Título automático ──────────────────────────────────────────────────────────

def _generate_title(transcript_excerpt: str) -> str:
    """
    Genera un título corto y descriptivo para el audio usando Gemini.
    Usa solo los primeros ~800 chars del transcript para no gastar tokens.
    Devuelve un string, o un título genérico de fallback si falla.
    """
    if not _gemini:
        return 'Audio sin título'

    excerpt = transcript_excerpt[:800].strip()
    prompt  = (
        'Genera un título corto (máximo 8 palabras) y descriptivo para esta '
        'grabación de audio. Debe capturar el tema principal, sin entrecomillar '
        'ni añadir puntuación al final. Solo el título, nada más.\n\n'
        f'Fragmento de la transcripción:\n{excerpt}'
    )
    try:
        resp = _gemini.chat.completions.create(
            model=GEMINI_MODEL_GENERAL,
            messages=[{'role': 'user', 'content': prompt}],
            max_tokens=30,
            temperature=0.4,
        )
        title = resp.choices[0].message.content.strip().strip('"\'')
        logger.info(f'[Audio] Título generado: {title!r}')
        return title or 'Audio de GaIA'
    except Exception as e:
        logger.warning(f'[Audio] Error generando título: {e}')
        return 'Audio de GaIA'


# ── Endpoints ─────────────────────────────────────────────────────────────────

@audio_bp.route('/audio/upload', methods=['POST'])
@require_creator
def upload_audio():
    """
    Recibe un archivo de audio, lo procesa completo y guarda todo en un
    solo flujo:
      1. Recibe el archivo (multipart/form-data, campo 'audio')
      2. Sube el binario a Supabase Storage
      3. Transcribe con Groq Whisper (verbose_json con timestamps)
      4. Chunkea el transcript en fragmentos de ~150 palabras
      5. Genera embedding HuggingFace por chunk
      6. Genera título automático con Gemini
      7. Inserta audio_files + audio_chunks en la DB
    """
    user_id = g.user_id

    if 'audio' not in request.files:
        return jsonify({'error': 'No se recibió ningún archivo de audio'}), 400

    file       = request.files['audio']
    filename   = file.filename or 'audio.webm'
    audio_bytes = file.read()
    file_size  = len(audio_bytes)

    # Validación de tamaño (Groq Whisper: máx 25MB)
    max_bytes = 25 * 1024 * 1024
    if file_size > max_bytes:
        return jsonify({
            'error': f'El archivo supera los 25MB permitidos por Whisper '
                     f'({file_size / 1024 / 1024:.1f}MB). '
                     f'Graba en fragmentos más cortos.'
        }), 413

    logger.info(f'[Audio] Upload de {user_id}: {filename} ({file_size} bytes)')

    # 1. Subir binario a Supabase Storage
    import uuid as _uuid
    file_uuid    = str(_uuid.uuid4())
    storage_path = f'{user_id}/{file_uuid}_{filename}'
    ext          = filename.rsplit('.', 1)[-1].lower() if '.' in filename else 'webm'
    mime_map     = {'webm': 'audio/webm', 'mp3': 'audio/mpeg', 'mp4': 'audio/mp4',
                    'wav': 'audio/wav',   'ogg': 'audio/ogg',  'm4a': 'audio/mp4'}
    mime         = mime_map.get(ext, 'audio/webm')

    if not _upload_to_storage(storage_path, audio_bytes, mime):
        return jsonify({'error': 'Error al guardar el audio en Storage'}), 500

    # 2. Transcribir con Whisper
    transcription = _transcribe(audio_bytes, filename)
    if not transcription:
        _delete_from_storage(storage_path)
        return jsonify({'error': 'Error al transcribir el audio'}), 500

    transcript_full = transcription.text
    segments        = transcription.segments
    duration        = segments[-1].end if segments else 0.0

    # 3. Chunkear el transcript
    raw_chunks = _chunk_transcript(segments)

    # 4. Generar título automático
    title = _generate_title(transcript_full)

    # 5. Insertar audio_file en la DB
    audio_row = db_one(
        """
        INSERT INTO audio_files
            (user_id, filename, storage_path, duration_seconds,
             transcript_full, title, file_size_bytes, visibility)
        VALUES (%s, %s, %s, %s, %s, %s, %s, 'creator')
        RETURNING id
        """,
        (user_id, filename, storage_path, duration,
         transcript_full, title, file_size)
    )
    if not audio_row:
        _delete_from_storage(storage_path)
        return jsonify({'error': 'Error al guardar los metadatos del audio'}), 500

    audio_file_id = str(audio_row['id'])

    # 6. Insertar chunks con embeddings
    chunks_ok = 0
    for i, chunk in enumerate(raw_chunks):
        embedding = embed_text(chunk['content'])
        source    = f'audio:{filename}@{chunk["start_time"]:.0f}s'

        ok = db_run(
            """
            INSERT INTO audio_chunks
                (audio_file_id, chunk_index, content, start_time, end_time,
                 embedding, visibility)
            VALUES (%s, %s, %s, %s, %s, %s, 'creator')
            """,
            (audio_file_id, i, chunk['content'],
             chunk['start_time'], chunk['end_time'],
             embedding)
        )
        if ok:
            chunks_ok += 1

    logger.info(f'[Audio] Procesado OK: id={audio_file_id}, '
                f'chunks={chunks_ok}/{len(raw_chunks)}, título={title!r}')

    return jsonify({
        'id':              audio_file_id,
        'title':           title,
        'filename':        filename,
        'duration':        duration,
        'chunks':          chunks_ok,
        'transcript_preview': transcript_full[:300] + ('...' if len(transcript_full) > 300 else ''),
    }), 201


@audio_bp.route('/audio/list', methods=['GET'])
@require_creator
def list_audios():
    """Lista todos los audios del creator con metadatos, sin el transcript completo."""
    user_id = g.user_id
    rows = db_all(
        """
        SELECT id, filename, title, duration_seconds, file_size_bytes,
               visibility, uploaded_at,
               (SELECT COUNT(*) FROM audio_chunks WHERE audio_file_id = af.id) AS chunk_count
        FROM audio_files af
        WHERE user_id = %s
        ORDER BY uploaded_at DESC
        """,
        (user_id,)
    )
    return jsonify({'audios': rows or []})


@audio_bp.route('/audio/<audio_id>/title', methods=['PATCH'])
@require_creator
def update_title(audio_id: str):
    """Actualiza el título de un audio. Solo el propietario puede editarlo."""
    user_id = g.user_id
    data    = request.get_json() or {}
    title   = (data.get('title') or '').strip()

    if not title:
        return jsonify({'error': 'El título no puede estar vacío'}), 400

    ok = db_run(
        "UPDATE audio_files SET title = %s WHERE id = %s AND user_id = %s",
        (title, audio_id, user_id)
    )
    if not ok:
        return jsonify({'error': 'Audio no encontrado o sin permiso'}), 404

    logger.info(f'[Audio] Título actualizado: {audio_id} → {title!r}')
    return jsonify({'id': audio_id, 'title': title})


@audio_bp.route('/audio/<audio_id>/promote', methods=['PATCH'])
@require_creator
def promote_audio(audio_id: str):
    """
    Promueve un audio a visibilidad 'all': sus chunks pasan a ser base de
    conocimiento general, visible para todos los usuarios en las búsquedas
    de knowledge. Esta acción es reversible llamando con visibility='creator'.
    """
    user_id    = g.user_id
    data       = request.get_json() or {}
    visibility = data.get('visibility', 'all')

    if visibility not in ('creator', 'all'):
        return jsonify({'error': "visibility debe ser 'creator' o 'all'"}), 400

    # Actualizar el archivo y todos sus chunks a la vez
    ok1 = db_run(
        "UPDATE audio_files SET visibility = %s WHERE id = %s AND user_id = %s",
        (visibility, audio_id, user_id)
    )
    ok2 = db_run(
        """
        UPDATE audio_chunks SET visibility = %s
        WHERE audio_file_id = %s
          AND EXISTS (
            SELECT 1 FROM audio_files
            WHERE id = %s AND user_id = %s
          )
        """,
        (visibility, audio_id, audio_id, user_id)
    )

    if not (ok1 and ok2):
        return jsonify({'error': 'Audio no encontrado o sin permiso'}), 404

    accion = 'promovido a conocimiento general' if visibility == 'all' else 'revertido a privado'
    logger.info(f'[Audio] {audio_id} {accion}')
    return jsonify({'id': audio_id, 'visibility': visibility, 'status': accion})


@audio_bp.route('/audio/<audio_id>', methods=['DELETE'])
@require_creator
def delete_audio(audio_id: str):
    """
    Elimina un audio: primero borra el binario de Storage, luego los chunks
    y el registro de la DB (los chunks se borran en cascada por FK).
    """
    user_id = g.user_id

    # Recuperar storage_path antes de borrar
    row = db_one(
        "SELECT storage_path FROM audio_files WHERE id = %s AND user_id = %s",
        (audio_id, user_id)
    )
    if not row:
        return jsonify({'error': 'Audio no encontrado o sin permiso'}), 404

    # Borrar de Storage (no bloqueante si falla — la DB sigue limpia)
    storage_ok = _delete_from_storage(row['storage_path'])
    if not storage_ok:
        logger.warning(f'[Audio] No se pudo borrar de Storage: {row["storage_path"]}')

    # Borrar de la DB (cascada elimina audio_chunks automáticamente)
    db_run(
        "DELETE FROM audio_files WHERE id = %s AND user_id = %s",
        (audio_id, user_id)
    )

    logger.info(f'[Audio] Eliminado: {audio_id}')
    return jsonify({'id': audio_id, 'deleted': True})
