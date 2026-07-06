import os

# ── LLM ───────────────────────────────────────────────────────────────────────
GROQ_API_KEY = os.environ.get('GROQ_API_KEY', '')

# Cadenas de modelos por dominio, en orden de preferencia. Si el primero
# devuelve rate-limit (429) o payload-too-large (413 por exceso de TPM),
# se prueba el siguiente de la lista. Todos gratuitos en Groq a fecha jul-2026.
#
# Límites reales del tier gratuito (consultados en console.groq.com/settings/limits
# el 6-jul-2026) — ojo, son POR MODELO, no compartidos entre todos:
#
#   meta-llama/llama-4-scout-17b-16e-instruct : 30K TPM / 500K TPD  ← el más holgado
#   llama-3.3-70b-versatile                   : 12K TPM / 100K TPD
#   llama-3.1-8b-instant                      :  6K TPM / 500K TPD
#   openai/gpt-oss-120b                       :  8K TPM / 200K TPD
#   openai/gpt-oss-20b                        :  8K TPM / 200K TPD
#   qwen/qwen3.6-27b                          :  8K TPM / 200K TPD
#
# NOTA (jul-2026): la familia GPT-OSS / Qwen3.6 tiene un TPM (8K) más bajo de
# lo que se pensaba inicialmente — con el ADN (~4-5K tokens) + RAG + memoria
# cruzada + perfil de usuario, una sola petición puede consumir gran parte de
# esa cuota, provocando 413 en los tres modelos de golpe tras pocos mensajes
# seguidos. Se migra la cadena principal a llama-4-scout / llama-3.3, que
# tienen bastante más margen (30K y 12K TPM respectivamente).
GROQ_MODELS_GENERAL = [
    'meta-llama/llama-4-scout-17b-16e-instruct',  # principal — mejor margen de TPM/TPD con diferencia
    'llama-3.3-70b-versatile',                    # fallback — buena calidad, TPM más ajustado
    'llama-3.1-8b-instant',                       # último recurso — rápido, TPD muy alto (500K)
]

GROQ_MODELS_ASTROLOGY = [
    'llama-3.1-8b-instant',                       # principal — rápido y barato para datos estructurados
    'meta-llama/llama-4-scout-17b-16e-instruct',  # fallback — mucho margen si el pequeño se satura
    'llama-3.3-70b-versatile',                    # último recurso
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