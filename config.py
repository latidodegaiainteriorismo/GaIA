import os

# ── LLM ───────────────────────────────────────────────────────────────────────
GROQ_API_KEY = os.environ.get('GROQ_API_KEY', '')

# Cadenas de modelos por dominio, en orden de preferencia. Si el primero
# devuelve rate-limit (429) o payload-too-large (413 por exceso de TPM),
# se prueba el siguiente de la lista. Todos gratuitos en Groq a fecha jul-2026.
#
# NOTA: llama-3.1-8b-instant y llama-3.3-70b-versatile fueron anunciados
# como deprecados por Groq el 17-jun-2026. Se migra a la familia GPT-OSS /
# Qwen3.6, con mejor cuota de TPM en el tier gratuito (250K vs 12K).
GROQ_MODELS_GENERAL = [
    'openai/gpt-oss-120b',   # principal — mejor calidad de razonamiento/conversación
    'qwen/qwen3.6-27b',      # fallback — familia de pesos distinta, mismo TPM (250K)
    'openai/gpt-oss-20b',    # último recurso — rápido, más TPM (250K), calidad menor
]

GROQ_MODELS_ASTROLOGY = [
    'openai/gpt-oss-20b',    # principal — riguroso con datos estructurados, rápido, barato
    'qwen/qwen3.6-27b',      # fallback — mayor capacidad si 20b se queda corto
    'openai/gpt-oss-120b',   # último recurso — comparte cuota con el chat general
]

# Retrocompatibilidad: código legado que aún importe GROQ_MODEL directamente
# sigue funcionando (usa el principal de la cadena general).
GROQ_MODEL          = GROQ_MODELS_GENERAL[0]
GROQ_MODEL_FALLBACK = GROQ_MODELS_GENERAL[1]

# ── BASE DE DATOS ─────────────────────────────────────────────────────────────
DATABASE_URL = os.environ.get('DATABASE_URL', '')

# ── AUTH ──────────────────────────────────────────────────────────────────────
GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID', '')

# Email del desarrollador — GaIA lo reconoce automáticamente y habilita
# comandos especiales (editar su propio ADN, ver prompts/dev_commands.py)
DEVELOPER_EMAIL = 'adrian.lozano.roca@gmail.com'

# ── TTS ───────────────────────────────────────────────────────────────────────
EDGE_TTS_VOICE = os.environ.get('EDGE_TTS_VOICE', 'es-ES-ElviraNeural')

# ── EMBEDDINGS (Fase 1) ───────────────────────────────────────────────────────
HUGGINGFACE_API_KEY    = os.environ.get('HUGGINGFACE_API_KEY', '')
HUGGINGFACE_MODEL      = 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'
EMBEDDING_DIMENSIONS   = 384

# ── MEMORIA ───────────────────────────────────────────────────────────────────
MAX_CONV_TOKENS        = 1200   # Comprimir conversación si supera este límite
MEMORY_TOP_K           = 3      # Top-K episodios relevantes del usuario
KNOWLEDGE_TOP_K        = 2      # Top-K chunks de la base de conocimiento (bajado de 5 para ahorrar tokens/día en Groq)
IMPORTANCE_THRESHOLD   = 7      # Mínimo (sobre 10) para guardar episodio
