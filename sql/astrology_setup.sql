-- Ejecuta esto en el SQL Editor de Supabase.
-- Asume que la tabla `conversations` ya existe con user_id UUID (esquema actual de GaIA).

CREATE TABLE user_birth_charts (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id             UUID NOT NULL UNIQUE,   -- un usuario = una carta natal activa
    birth_datetime_utc  TIMESTAMPTZ NOT NULL,
    birth_lat           DOUBLE PRECISION NOT NULL,
    birth_lon           DOUBLE PRECISION NOT NULL,
    birth_place         TEXT,
    sun_sign            TEXT,
    moon_sign           TEXT,
    rising_sign         TEXT,
    full_chart          JSONB NOT NULL,          -- BirthChart.to_dict() completo
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_user_birth_charts_user ON user_birth_charts(user_id);
