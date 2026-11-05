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
- **Ojo: Render NO lee el `Procfile`**: usa el *Start Command* de su panel (Settings → Build & Deploy). El 3-nov-2026 los registros mostraron `gunicorn/workers/sync.py` y WORKER TIMEOUT a los 30 s, es decir, arrancaba sin `--timeout 120` ni hilos. El comando correcto es `gunicorn app:app --timeout 120 --workers 1 --threads 3` y hay que ponerlo allí; el `Procfile` solo documenta la intención.
- Voz: Edge TTS (Elvira) tarda ~7 ms/carácter. La web manda `audio_aparte: true` a `/chat` y este NO sintetiza: devuelve el texto al instante y la web pide la voz por bloques (~500 caracteres el primero, ≤1200 los demás) a `/tts`, así se lee la respuesta entera. Sin esa marca (modo temporal, webs antiguas) el audio va dentro de `/chat` y se limita a 2200 caracteres y a 20 s (`tts.py`); si se pasa, la respuesta sale sin audio, nunca con error. Alternativa probada (nov-2026): Gemini TTS (`gemini-3.8-flash-lite-tts`, ~3 veces más lento, más expresivo); el nivel gratuito de Google puede usar el contenido para mejorar sus productos. Los embeddings los genera Gemini (`gemini-embedding-001`, 384 dimensiones, misma `GEMINI_API_KEY` que el chat; antes HuggingFace, que se quedó sin créditos el 3-nov-2026). Un 401/402/403 los pausa 30 min y un 429 un minuto (`embeddings.py`). Los vectores de modelos distintos no son comparables: tras cambiar de modelo hay que ejecutar `scripts/reembeber.py` (solo `messages.embedding` se busca por vector).
- Servidor Flask con **1 worker gunicorn y 3 hilos** (`Procfile`) en plan Free de Render (512 MB): así una respuesta larga (LLM + voz) o la pasada de `/maintenance/consolidate` (UptimeRobot, cada pocos minutos) no bloquean a los demás. `--timeout 120` evita el `CRITICAL WORKER TIMEOUT` ya sufrido en TTS. Si Render empieza a matar el worker por memoria (OOM), bajar `--threads` a 1.
- `db.py` usa un **pool de conexiones** (psycopg2 `ThreadedConnectionPool`, máx. 8): reutiliza conexiones, descarta las que Supabase cierra por inactividad y reintenta una vez. Las lecturas (`db_all`) van en autocommit y **nunca deben usarse para escribir**.
- **Pruebas**: `pip install -r requirements.txt pytest && python -m pytest tests` (sin BD ni red). GitHub Actions las ejecuta en cada push (`.github/workflows/tests.yml`); si salen en rojo, no te fíes del despliegue. Al editar desde la web de GitHub, mira que el check quede en verde.
- **Conocimiento privado (`visibility='creator'`)**: `search_knowledge(..., visibilities=('all',))` solo ve lo público; `search_knowledge_creator` pasa `('creator','all')`. Los documentos de terceros (p.ej. transcripciones de YouTube de José Millán) se guardan privados. Para publicarlos: `UPDATE knowledge_chunks SET visibility='all' WHERE source LIKE '%(José Millán - YouTube)'`.
- **Ingesta de canales de YouTube**: `scripts/ingest_youtube.py` (ver su cabecera). Se ejecuta EN LOCAL, nunca en Render/GitHub Actions (YouTube bloquea IPs de servidores y limita con 429 si se piden muchas seguidas: ritmo lento, caché en `~/.gaia_youtube/`). Filtra por título solo lo práctico (carta natal, casas, planetas, aspectos...) y descarta lunaciones/previsiones/actualidad. La búsqueda de conocimiento es solo por texto completo (FTS en español): los fragmentos llevan el título delante para que casen.
- **Astrología (motor y lectura)**: posiciones GEOCÉNTRICAS y con `epoch='date'` (zodiaco tropical de la fecha); sin eso salían ~0,36° bajas en 2026 y la Luna hasta ~1° (se verificó contra carta-natal.es: `tests/test_astro_angulos.py`). Ascendente/Medio Cielo verificados contra un cálculo independiente. Los 4 ángulos (`astro_core/angulos.py`) entran en aspectos natales y de tránsito con orbes estrechos; `astro_core/relevancia.py` pondera lento/rápido; el contexto lleva la casa de tránsito y la marca ℞. **Sin hora de nacimiento** (`time_known=False`, también por chat si no se dice la hora) no hay ángulos ni casas. Tras cambiar el motor hay que recalcular las cartas guardadas: `scripts/recalcular_cartas.py` (sin `--aplicar` solo muestra; con él hace copia de seguridad en `~/.gaia_backup/`, nunca en el repo).
- El SQL de las tablas de clasificación está en `sql/classification_setup.sql`. Las demás tablas se crearon a mano en Supabase sin migración versionada.

## Arquitectura del backend
- **LLM principal**: Gemini Flash-Lite vía endpoint OpenAI-compatible de Google (migrado desde Groq). Groq se mantiene para transcripción Whisper y fallback de emergencia.
- **Sistema de memoria en 4 fases**: user_synthesis, memoria episódica, búsqueda híbrida FTS+vectorial.
  - `user_synthesis`: consolidación asíncrona vía `/maintenance/consolidate`, pingeado por UptimeRobot.
  - Memoria episódica con expansión de temas vía LLM (`_expand_memory_themes`).
  - Búsqueda híbrida FTS + vectorial usando embeddings de Gemini.
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
