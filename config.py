import os

# ── LLM ───────────────────────────────────────────────────────────────────────
GROQ_API_KEY = os.environ.get('GROQ_API_KEY', '')
GROQ_MODEL   = 'llama-3.3-70b-versatile'

# ── BASE DE DATOS ─────────────────────────────────────────────────────────────
DATABASE_URL = os.environ.get('DATABASE_URL', '')

# ── AUTH ──────────────────────────────────────────────────────────────────────
GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID', '')

# ── TTS ───────────────────────────────────────────────────────────────────────
EDGE_TTS_VOICE = os.environ.get('EDGE_TTS_VOICE', 'es-ES-ElviraNeural')

# ── EMBEDDINGS (Fase 1) ───────────────────────────────────────────────────────
HUGGINGFACE_API_KEY    = os.environ.get('HUGGINGFACE_API_KEY', '')
HUGGINGFACE_MODEL      = 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'
EMBEDDING_DIMENSIONS   = 384

# ── MEMORIA ───────────────────────────────────────────────────────────────────
MAX_CONV_TOKENS        = 1200   # Comprimir conversación si supera este límite
MEMORY_TOP_K           = 3      # Top-K episodios relevantes del usuario
KNOWLEDGE_TOP_K        = 5      # Top-K chunks de la base de conocimiento
IMPORTANCE_THRESHOLD   = 7      # Mínimo (sobre 10) para guardar episodio
