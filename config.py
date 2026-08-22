import os

# ── LLM ───────────────────────────────────────────────────────────────────────
#
# MIGRACIÓN (19-ago-2026): de Groq (gratis, 8K TPM) a Gemini (de pago, ~1M de
# contexto). Motivo: el tier gratuito de Groq resultó estructuralmente
# insuficiente para el volumen de contexto que GaIA necesita (ADN ~4.500
# tokens + memoria + knowledge + astrología, fácilmente 10-15K tokens en
# preguntas con varios documentos activados), causando recortes agresivos y
# pérdida de memoria/conocimiento de forma recurrente. Ver conversación de
# diagnóstico del 19-ago-2026 para el detalle completo.
#
# Gemini se llama a través de su endpoint compatible con la API de OpenAI
# (ai.google.dev/gemini-api/docs/openai) — mismo cliente `openai`, solo
# cambia base_url, api_key y nombre de modelo. Esto significa que casi todo
# el código que ya usaba `_client.chat.completions.create(...)` sigue
# funcionando sin tocar, incluido `reasoning_effort` (soportado por Gemini
# a través de este endpoint).
GEMINI_API_KEY  = os.environ.get('GEMINI_API_KEY', '')
GEMINI_BASE_URL = 'https://generativelanguage.googleapis.com/v1beta/openai/'

# Gemini 3.5 Flash-Lite: elegido tras comparar coste/beneficio con Gemini
# 3.6/3.7 Flash y Claude Haiku 4.5 (ver conversación de decisión). Sale más
# barato que las otras opciones serias evaluadas y la prueba en el
# Playground de AI Studio (con el ADN completo + contexto simulado) mostró
# calidad conceptual y densidad asociativa acordes al ADN de GaIA.
GEMINI_MODEL_GENERAL   = 'gemini-3.5-flash-lite'
GEMINI_MODEL_ASTROLOGY = 'gemini-3.5-flash-lite'

# Retrocompatibilidad: código de preproceso (query_router.py, user_profile.py,
# memory_search.py) importaba GROQ_MODEL/GROQ_MODEL_FALLBACK directamente.
# Apuntan ahora al mismo modelo Gemini — con el margen de contexto/TPM que
# da Gemini, ya no hace falta la lógica de "modelo distinto para evitar
# colisión de cuota" que existía cuando todo compartía 8K TPM en Groq.
GEMINI_MODEL          = GEMINI_MODEL_GENERAL
GEMINI_MODEL_FALLBACK = GEMINI_MODEL_GENERAL

# ── GROQ (fallback de emergencia + Whisper) ────────────────────────────────────
#
# Se mantiene configurado pero DEJA de ser el proveedor principal de LLM. Si
# algún día Gemini tiene una caída de servicio, llm.py puede recurrir a esta
# cadena como red de seguridad. Adicionalmente, Groq se usa para Whisper
# (transcripción de audio) — es gratuito con la key existente y no requiere
# ninguna configuración adicional.
GROQ_API_KEY = os.environ.get('GROQ_API_KEY', '')
GROQ_MODELS_GENERAL = [
    'openai/gpt-oss-120b',
    'qwen/qwen3.6-27b',
    'openai/gpt-oss-20b',
]
GROQ_MODELS_ASTROLOGY = [
    'openai/gpt-oss-20b',
    'qwen/qwen3.6-27b',
    'openai/gpt-oss-120b',
]

# ── BASE DE DATOS ─────────────────────────────────────────────────────────────
DATABASE_URL = os.environ.get('DATABASE_URL', '')

# ── AUTH ──────────────────────────────────────────────────────────────────────
GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID', '')

# Email del desarrollador — GaIA lo reconoce automáticamente y habilita
# comandos especiales (editar su propio ADN, ver prompts/dev_commands.py)
DEVELOPER_EMAIL = 'adrian.lozano.roca@gmail.com'

# Email del creator — rol especial para Mónica y Adrián como co-creadores.
# Permisos adicionales sobre los del desarrollador:
#   - Acceso a fragmentos literales de cualquier documento (incluido el ADN)
#   - Subida de audios como preguntas, con transcripción y almacenamiento chunkeado
#   - Los audios subidos quedan como memoria de conversación privada hasta
#     que decidan promoverlos a base de conocimiento general ('all')
CREATOR_EMAIL = 'latidodegaiainteriorismo@gmail.com'

# ── SUPABASE STORAGE ──────────────────────────────────────────────────────────
# Usado para almacenar los archivos de audio binarios de forma persistente
# (Render no tiene filesystem persistente entre deploys). El bucket gaia-audios
# debe estar creado en el dashboard de Supabase con visibilidad privada.
# SUPABASE_SERVICE_KEY es la secret key (sb_secret_...) — nunca la publishable.
SUPABASE_URL          = os.environ.get('SUPABASE_URL', '')
SUPABASE_SERVICE_KEY  = os.environ.get('SUPABASE_SERVICE_KEY', '')
SUPABASE_AUDIO_BUCKET = 'gaia-audios'

# ── WHISPER (transcripción de audio) ──────────────────────────────────────────
# Se usa el cliente Groq ya configurado (GROQ_API_KEY) — Groq ofrece
# Whisper Large v3 gratuito con límites generosos, suficiente para audios
# de hasta 30 minutos. No requiere key adicional.
WHISPER_MODEL      = 'whisper-large-v3'
AUDIO_MAX_MB       = 25    # límite de Groq Whisper en MB
AUDIO_WARN_MINUTES = 20    # aviso al usuario en el frontend
AUDIO_CHUNK_WORDS  = 150   # palabras por chunk de transcript (~45-60s de audio)

# ── TTS ───────────────────────────────────────────────────────────────────────
EDGE_TTS_VOICE = os.environ.get('EDGE_TTS_VOICE', 'es-ES-ElviraNeural')

# ── EMBEDDINGS (Fase 1) ───────────────────────────────────────────────────────
HUGGINGFACE_API_KEY    = os.environ.get('HUGGINGFACE_API_KEY', '')
HUGGINGFACE_MODEL      = 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'
EMBEDDING_DIMENSIONS   = 384

# ── MEMORIA ───────────────────────────────────────────────────────────────────
MAX_CONV_TOKENS      = 1200  # Comprimir conversación si supera este límite
MEMORY_TOP_K         = 3     # Top-K episodios relevantes del usuario
KNOWLEDGE_TOP_K      = 2     # Top-K chunks de la base de conocimiento
IMPORTANCE_THRESHOLD = 7     # Mínimo (sobre 10) para guardar episodio
