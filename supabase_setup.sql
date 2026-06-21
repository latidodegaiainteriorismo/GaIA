-- Ejecuta esto en el SQL Editor de Supabase
CREATE TABLE gaia_conversations (
  id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  session_id  TEXT NOT NULL,
  role        TEXT NOT NULL,       -- 'user' o 'assistant'
  content     TEXT NOT NULL,
  created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_gaia_session ON gaia_conversations(session_id, created_at);
