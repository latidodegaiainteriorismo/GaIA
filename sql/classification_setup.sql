-- Estructura Basal / clasificación automática de conversaciones del creator
-- (classification.py, routes/learnings.py). Ya ejecutado a mano en Supabase;
-- se guarda aquí para poder reconstruir la base de datos. Es idempotente.
-- Ejecuta en el SQL Editor de Supabase. GET /health/schema comprueba que exista.

ALTER TABLE knowledge_chunks
    ADD COLUMN IF NOT EXISTS visibility TEXT DEFAULT 'all'
        CHECK (visibility IN ('creator', 'all'));
CREATE INDEX IF NOT EXISTS knowledge_chunks_visibility_idx ON knowledge_chunks(visibility);

CREATE TABLE IF NOT EXISTS basal_notes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    content TEXT NOT NULL,
    source_excerpt TEXT,
    conversation_id UUID REFERENCES conversations(id) ON DELETE SET NULL,
    created_by UUID REFERENCES users(id) ON DELETE SET NULL,
    active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS basal_notes_active_idx ON basal_notes(active) WHERE active = true;

CREATE TABLE IF NOT EXISTS conversation_classification_state (
    conversation_id UUID PRIMARY KEY REFERENCES conversations(id) ON DELETE CASCADE,
    last_classified_message_id UUID REFERENCES messages(id) ON DELETE SET NULL,
    classified_at TIMESTAMPTZ DEFAULT NOW()
);
