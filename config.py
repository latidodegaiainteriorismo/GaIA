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
# NOTA (25-jul-2026): meta-llama/llama-4-scout-17b-16e-instruct fue retirado
# por Groq el 17-jul-2026 (404 model_not_found). llama-3.3-70b-versatile y
# llama-3.1-8b-instant también están deprecados, con apagado programado para
# el 16-ago-2026 — así que se migran también, aunque técnicamente sigan
# funcionando por ahora. Cadena nueva usando los 3 modelos recomendados por
# Groq como reemplazo estable (ver console.groq.com/docs/deprecations).
#
# Aviso: estos 3 modelos comparten el mismo límite bajo de TPM (8K) que
# teníamos con gpt-oss-120b/20b y qwen3.6-27b antes de encontrar
# llama-4-scout — el margen es más ajustado que con el modelo anterior.
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

# NOTA (13-ago-2026): GROQ_MODEL se usa para las llamadas de PRE-PROCESO
# (query_router.py, detección de perfil en user_profile.py, expansión de
# memoria) — nunca para la respuesta final de GaIA, que siempre recorre
# GROQ_MODELS_GENERAL desde el principio. Antes GROQ_MODEL apuntaba a
# GROQ_MODELS_GENERAL[0] (gpt-oss-120b) — el MISMO modelo que la respuesta
# principal intenta primero — así que un solo mensaje del usuario disparaba
# dos llamadas distintas al mismo modelo, compitiendo por el mismo cupo de
# 8K TPM en el mismo minuto. Eso causó, el mismo día, fallos de router (JSON
# cortado a mitad de generación por falta de cupo) y 413 en cadena en los
# tres modelos a la vez.
#
# Apunta ahora al último de la cadena general (el que menos se usa, porque
# solo entra en juego si los otros dos ya devolvieron 429/413) para
# minimizar la colisión. No la elimina del todo — solo hay 3 modelos
# estables disponibles en total en el tier gratuito — pero reduce mucho la
# probabilidad de que el router y la respuesta principal choquen a la vez.
GROQ_MODEL          = GROQ_MODELS_GENERAL[2]   # gpt-oss-20b
GROQ_MODEL_FALLBACK = GROQ_MODELS_GENERAL[1]   # qwen3.6-27b

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


