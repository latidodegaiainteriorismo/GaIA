-- Ejecuta esto en el SQL Editor de Supabase.
-- Este script hace DOS cosas:
--   1. Crea la tabla user_profiles (datos personales del usuario, con onboarding único)
--   2. Migra user_birth_charts para soportar VARIAS cartas por usuario (la propia,
--      pareja, hijos, familiares...) en vez de una única carta por usuario.

-- ─────────────────────────────────────────────────────────────────────────
-- 1. PERFIL DE USUARIO
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE user_profiles (
    id                    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id               UUID NOT NULL UNIQUE,
    preferred_name        TEXT,               -- cómo quiere que GaIA le llame
    profile_data          JSONB DEFAULT '{}', -- datos libres: familia, gustos, estudios, profesión...
    onboarding_completed  BOOLEAN DEFAULT FALSE, -- si ya se le hizo la pregunta inicial de qué compartir
    created_at            TIMESTAMPTZ DEFAULT NOW(),
    updated_at            TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_user_profiles_user ON user_profiles(user_id);


-- ─────────────────────────────────────────────────────────────────────────
-- 2. CARTAS NATALES — MIGRACIÓN A MÚLTIPLES PERSONAS POR USUARIO
-- ─────────────────────────────────────────────────────────────────────────
-- Si ya ejecutaste el script anterior (astrology_setup.sql) y tienes la
-- tabla user_birth_charts con UNIQUE(user_id), esto la sustituye por una
-- versión que permite varias filas por usuario, una por persona.
--
-- IMPORTANTE: si ya tienes cartas natales guardadas de usuarios reales y
-- quieres conservarlas, avisa antes de ejecutar el DROP — este script las
-- borraría. Si tu tabla está vacía o en pruebas, procede sin más.

DROP TABLE IF EXISTS user_birth_charts;

CREATE TABLE user_birth_charts (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id             UUID NOT NULL,          -- dueño del perfil (quien tiene la cuenta)
    person_label        TEXT NOT NULL,          -- nombre/etiqueta de la persona: "yo", "Marco (hijo)", "María (pareja)"
    relationship        TEXT,                   -- opcional: "self", "hijo", "pareja", "madre", etc.
    birth_datetime_utc  TIMESTAMPTZ NOT NULL,
    birth_lat           DOUBLE PRECISION NOT NULL,
    birth_lon           DOUBLE PRECISION NOT NULL,
    birth_place         TEXT,
    sun_sign            TEXT,
    moon_sign           TEXT,
    rising_sign         TEXT,
    full_chart          JSONB NOT NULL,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW(),

    -- Un usuario no puede tener dos cartas con la misma etiqueta (evita duplicados
    -- accidentales de "Marco" dos veces), pero sí varias etiquetas distintas.
    UNIQUE(user_id, person_label)
);

CREATE INDEX idx_user_birth_charts_user ON user_birth_charts(user_id);
