# CLAUDE.md — GaIA (backend)

## Qué es este proyecto
GaIA es una app de compañía espiritual con IA, co-creada por Adrián (arquitectura técnica y desarrollo completo) y Mónica Martos (contenido/teoría). El eje conceptual es espiritualidad, consciencia y el marco filosófico de la "Era de Acuario".

## Repos relacionados
- Backend (este repo): `latidodegaiainteriorismo/GaIA`
- Frontend: `latidodegaiainteriorismo/GaIA-frontend`

## Infraestructura
- Backend desplegado en Render: https://gaia-2py8.onrender.com
- Frontend desplegado en Vercel: https://ga-ia-frontend.vercel.app
- Cuenta principal `latidodegaiainteriorismo@gmail.com` cubre GitHub, Render, Vercel, Supabase, ElevenLabs, HuggingFace
- Flujo de despliegue: commit directo a `main` → auto-deploy en Render. **No hay entorno de staging** — cualquier cambio probado en local antes de hacer push.
- Servidor Flask con **1 worker gunicorn** en plan Free de Render — cuidado con timeouts (ya hubo un incidente de `CRITICAL WORKER TIMEOUT` en TTS por exceder el timeout de 30s por defecto; el fix fue `--timeout 120` en el comando de arranque).

## Arquitectura del backend
- **LLM principal**: Gemini Flash-Lite vía endpoint OpenAI-compatible de Google (migrado desde Groq). Groq se mantiene para transcripción Whisper y fallback de emergencia.
- **Sistema de memoria en 4 fases**: user_synthesis, memoria episódica, búsqueda híbrida FTS+vectorial.
  - `user_synthesis`: consolidación asíncrona vía `/maintenance/consolidate`, pingeado por UptimeRobot.
  - Memoria episódica con expansión de temas vía LLM (`_expand_memory_themes`).
  - Búsqueda híbrida FTS + vectorial usando embeddings de HuggingFace.
- **Router de consultas** (`query_router.py`): catálogo de 14 documentos, Groq interpreta la consulta y devuelve documentos relevantes + keywords expandidas.
- **RAG**: PostgreSQL FTS con stemming en español + fallback ILIKE. Base de conocimiento: ~6,063 chunks de 18 documentos. Ingesta vía GitHub Actions (`ingest_knowledge.py`).
- **Audio**: pipeline de conocimiento por voz con Supabase Storage (bucket `gaia-audios`), tablas `audio_files`/`audio_chunks`, blueprint `routes/audio.py`.
- **Astrología**: módulo con Skyfield + fórmulas de Meeus (cartas natales, tránsitos, biwheel SVG), geocoding vía Nominatim, soporte multi-persona por perfil de usuario.
- **Rol de creador**: sistema de reconocimiento ligado a `adrian.lozano.roca@gmail.com`.
- **Módulo de juego** (diseñado, no confirmar estado de implementación sin revisar código): blueprint `/game`, tablas Supabase con prefijo `game_`.

## Cuidado con esto (bugs ya resueltos, no reintroducir)
- `_get_conv_messages`: el orden correcto es `DESC LIMIT 30` con reversión en Python — no cambiar a `ASC` directamente.
- Presupuesto de contexto adaptativo: `_MAX_CONTEXT_TOKENS = 20000` (subido desde 2600 tras migrar a Gemini); el orden de descarte prioriza truncar `cross_memory` en último lugar.
- `routes/chat.py` NO debe sustituirse por una versión antigua: la correcta usa `_ajustar_a_presupuesto`, modo creator (`search_knowledge_creator`), síntesis viva (`format_synthesis_context`), memoria profunda (`search_user_memory`) y guarda `sources`, además de las rutas `/messages` y `/tts`. El 28-ago-2026 se subió desde la web una versión vieja (ASC LIMIT 50, sin memoria ni modo creator) y GaIA estuvo dos meses sin ver los mensajes recientes de conversaciones largas.
- Esquema: `GET /health/schema` dice si existen las columnas/tablas que el código necesita (las migraciones se hicieron a mano en Supabase y un fallo ahí es silencioso).
- Colisión de modelos en el tier gratuito de Groq — ya resuelta, revisar antes de tocar la cadena de fallback de modelos.
- Rutas de blueprint: verificar SIEMPRE que una ruta nueva esté en el archivo donde se define su blueprint (ej. `/tts` debe estar en `chat.py`, no en `tts.py`, porque `chat_bp` se define en `chat.py`).

## El prompt (gaia_dna.txt)
Iterado conjuntamente con Mónica. Reglas activas a respetar en cualquier cambio:
- Densidad = información por palabra (evitar relleno).
- Arco de continuidad narrativa entre respuestas.
- Estructura titular → desarrollo.
- Regla anti-halago específica hacia Adrián/Mónica (no hacer cumplidos a los creadores; sí se permite imaginería poética).
- Una hipótesis, no un menú de opciones — vocabulario técnico preciso.
- Reglas anti-plantilla: máximo una pregunta por respuesta, no repetir estructura entre respuestas.

## Estilo de trabajo de Adrián
- Prefiere archivos completos reescritos en vez de diffs cuando el cambio es sustancial.
- Sin entorno de desarrollo local hasta ahora — viene de trabajar 100% desde GitHub web UI. Verificar en local con Claude Code antes de hacer push, no asumir que él lo hará manualmente.
- Antes de cualquier cambio con impacto en producción, avisar explícitamente qué se va a desplegar y confirmar antes de hacer commit/push a `main`.
