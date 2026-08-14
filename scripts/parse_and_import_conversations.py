"""
scripts/parse_and_import_conversations.py

Pipeline completo, automatico, para convertir transcripciones en bruto
(PDF o TXT exportadas de Gemini u otra IA) en conversaciones reales dentro
de GaIA, para UN usuario concreto — sin pasar por Claude ni por ningun chat.

Flujo:
  1. Lee cada fichero de conversations_import/raw/*.pdf y *.txt
  2. Extrae el texto (PDF via pypdf)
  3. Le pide a Groq que limpie artefactos de exportacion (citas duplicadas,
     encabezados repetidos, marcas tipo "PDF+2") y devuelva la conversacion
     como turnos {role, content} en JSON — troceando el texto en fragmentos
     si es muy largo, para no exceder el limite de tokens por llamada.
  4. Inserta la conversacion en `conversations` + `messages` para el
     usuario indicado (memoria PERSONAL — nunca toca knowledge_chunks).
  5. Mueve el fichero ya procesado a conversations_import/raw/done/, para
     que una relanzada del workflow no lo vuelva a procesar (idempotencia
     a nivel de fichero: lo que esta en done/ no se toca nunca mas).

SIMPLIFICACION DELIBERADA: cada fichero se importa como UNA sola
conversacion (no se subdivide automaticamente por temas — eso requeriria
un analisis mas fino que aqui, por simplicidad y fiabilidad, no se hace).
Si algun fichero concreto merece dividirse en varias conversaciones por
tema, es mejor tratarlo aparte.

Marcas de tiempo: no se conservan las horas originales de la conversacion
real (se renuncia a eso a proposito, ver conversacion de diseño) — cada
fichero importado recibe timestamps sinteticos, crecientes, a partir del
momento en que se ejecuta el workflow.

Variables de entorno requeridas:
  DATABASE_URL          - conexion a Supabase (ya existe como secret)
  GROQ_API_KEY           - para la limpieza/segmentacion (nuevo secret a añadir)
  GAIA_IMPORT_USER_ID    - UUID del usuario destino (por defecto, el unico
                            usuario para el que este historial tiene sentido —
                            ver DEFAULT_USER_ID mas abajo)
"""

import os
import sys
import json
import glob
import shutil
import logging
import time
from datetime import datetime, timedelta

import psycopg2
import psycopg2.extras
from groq import Groq

logging.basicConfig(level=logging.INFO, format='[import] %(message)s')
logger = logging.getLogger(__name__)

RAW_DIR = "conversations_import/raw"
DONE_DIR = "conversations_import/raw/done"

# Este pipeline se construyo especificamente para importar el historial de
# conversaciones de Gemini/GaIA de un unico usuario (latidodegaiainteriorismo@gmail.com).
# Si en el futuro se necesita importar historial de otro usuario, pasa
# GAIA_IMPORT_USER_ID explicitamente en el workflow_dispatch.
DEFAULT_USER_ID = "01aa0ba0-3cad-4a82-96c0-01513bb318d1"

# Modelos de Groq a intentar en orden — mismo patron de fallback que el
# resto de la app (ver config.py). Se reutiliza aqui la cadena general.
GROQ_MODELS = ["openai/gpt-oss-120b", "qwen/qwen3.6-27b", "openai/gpt-oss-20b"]

# Tamano maximo de texto por llamada a Groq (caracteres). Los ficheros mas
# largos se trocean en fragmentos de este tamano, respetando saltos de
# parrafo cuando es posible, para no exceder el presupuesto de tokens.
MAX_CHARS_PER_CALL = 9000

SECONDS_BETWEEN_MSGS = 120  # separacion sintetica entre mensajes


def extract_text(path: str) -> str:
    if path.lower().endswith(".pdf"):
        from pypdf import PdfReader
        reader = PdfReader(path)
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    with open(path, encoding="utf-8", errors="ignore") as f:
        return f.read()


def chunk_text(text: str, max_chars: int) -> list[str]:
    """Trocea el texto en fragmentos de tamano acotado, cortando por párrafos."""
    if len(text) <= max_chars:
        return [text]
    paragraphs = text.split("\n\n")
    chunks, current = [], ""
    for p in paragraphs:
        if len(current) + len(p) + 2 > max_chars and current:
            chunks.append(current)
            current = p
        else:
            current = current + "\n\n" + p if current else p
    if current:
        chunks.append(current)
    return chunks


def _call_groq_json(client: Groq, prompt: str) -> dict | list | None:
    """Llama a Groq con la cadena de modelos de fallback, esperando JSON."""
    for model in GROQ_MODELS:
        try:
            response = client.with_options(max_retries=1, timeout=45.0).chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=3000,
                temperature=0.2,
                reasoning_effort="low",
            )
            raw = response.choices[0].message.content.strip()
            raw = raw.replace("```json", "").replace("```", "").strip()
            return json.loads(raw)
        except Exception as e:
            logger.warning(f"  {model} fallo ({e}), probando siguiente")
            time.sleep(3)
            continue
    return None


def clean_chunk_to_turns(client: Groq, chunk: str, is_first: bool) -> list[dict]:
    """Le pide a Groq que convierta un fragmento de texto en bruto en turnos limpios."""
    prompt = f"""Vas a limpiar un fragmento de una transcripcion de chat exportada de Gemini
(que actua con el nombre "GaIA"). El texto de exportacion suele venir con
artefactos que hay que eliminar: marcas de cita como "PDF", "PDF+1", "TXT+2",
"YouTube" sueltas, y a veces el MISMO contenido aparece duplicado dos veces
seguidas (una version con formato, otra en texto corrido) — si detectas eso,
quedate solo con UNA copia.

No resumas ni acortes el contenido real de lo que dice el usuario o GaIA —
solo limpia artefactos de exportacion y duplicados. Preserva el texto real
integro, tal cual, en su idioma original (español).

Divide el fragmento en turnos alternos: 'user' (lo que escribe la persona)
y 'assistant' (lo que responde GaIA/Gemini).

Responde UNICAMENTE con JSON: una lista de objetos {{"role": "user"|"assistant", "content": "..."}}.
Si el fragmento empieza a mitad de un turno (continuacion del fragmento anterior),
el primer turno puede quedar incompleto — inclúyelo igualmente tal cual empieza.

TEXTO A LIMPIAR:
{chunk}"""
    result = _call_groq_json(client, prompt)
    if isinstance(result, list):
        return [t for t in result if isinstance(t, dict) and "role" in t and "content" in t]
    return []


def generate_title(client: Groq, first_chunk_turns: list[dict]) -> str:
    sample = "\n".join(f"{t['role']}: {t['content'][:300]}" for t in first_chunk_turns[:6])
    prompt = f"""Lee el inicio de esta conversacion entre un usuario y GaIA (un asistente
espiritual/de conocimiento) y devuelve SOLO un titulo breve en español
(6-10 palabras) que resuma el tema principal. Sin comillas, sin punto final.

INICIO DE LA CONVERSACION:
{sample}

Responde solo con el titulo, nada mas."""
    try:
        client_resp = client.with_options(max_retries=1, timeout=20.0).chat.completions.create(
            model=GROQ_MODELS[-1],
            messages=[{"role": "user", "content": prompt}],
            max_tokens=60,
            temperature=0.3,
            reasoning_effort="low",
        )
        title = client_resp.choices[0].message.content.strip().strip('"')
        return title if title else "Conversación importada"
    except Exception:
        return "Conversación importada"


def parse_file(client: Groq, path: str) -> dict | None:
    filename = os.path.basename(path)
    logger.info(f"Procesando {filename}")
    text = extract_text(path)
    if not text.strip():
        logger.warning(f"  {filename}: sin texto extraíble, se salta")
        return None

    chunks = chunk_text(text, MAX_CHARS_PER_CALL)
    logger.info(f"  {len(text)} caracteres, {len(chunks)} fragmento(s)")

    all_turns = []
    for i, chunk in enumerate(chunks):
        turns = clean_chunk_to_turns(client, chunk, is_first=(i == 0))
        logger.info(f"  fragmento {i+1}/{len(chunks)}: {len(turns)} turnos extraídos")
        all_turns.extend(turns)
        time.sleep(2)  # margen entre llamadas, respeta el TPM de Groq

    if not all_turns:
        logger.warning(f"  {filename}: no se pudo extraer ningún turno, se salta")
        return None

    title = generate_title(client, all_turns)
    return {"source_file": filename, "title": title, "turns": all_turns}


def import_conversation(cur, user_id: str, conv: dict, start: datetime) -> None:
    turns = conv["turns"]
    last_ts = start + timedelta(seconds=(len(turns) - 1) * SECONDS_BETWEEN_MSGS)

    cur.execute(
        "INSERT INTO conversations (user_id, title, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s) RETURNING id",
        (user_id, conv["title"], start, last_ts)
    )
    conv_id = cur.fetchone()[0]

    rows = []
    for i, turn in enumerate(turns):
        msg_ts = start + timedelta(seconds=i * SECONDS_BETWEEN_MSGS)
        rows.append((conv_id, user_id, turn["role"], turn["content"], msg_ts))

    psycopg2.extras.execute_values(
        cur,
        "INSERT INTO messages (conversation_id, user_id, role, content, created_at) VALUES %s",
        rows
    )
    logger.info(f'  Importada: "{conv["title"]}" ({len(turns)} mensajes)')


def main():
    user_id = os.environ.get("GAIA_IMPORT_USER_ID", "").strip() or DEFAULT_USER_ID
    database_url = os.environ.get("DATABASE_URL", "")
    groq_api_key = os.environ.get("GROQ_API_KEY", "")

    if not database_url or not groq_api_key:
        logger.error("Faltan DATABASE_URL o GROQ_API_KEY en el entorno.")
        sys.exit(1)

    os.makedirs(DONE_DIR, exist_ok=True)
    files = sorted(glob.glob(os.path.join(RAW_DIR, "*.pdf")) +
                   glob.glob(os.path.join(RAW_DIR, "*.txt")))
    files = [f for f in files if os.path.dirname(f) != DONE_DIR]

    if not files:
        logger.info(f"No hay ficheros nuevos en {RAW_DIR}/ — nada que importar.")
        return

    client = Groq(api_key=groq_api_key)
    conn = psycopg2.connect(database_url)
    conn.autocommit = False

    imported_count = 0
    now = datetime.utcnow()

    try:
        with conn.cursor() as cur:
            for offset, path in enumerate(files):
                conv = parse_file(client, path)
                if conv:
                    # Timestamps sinteticos, espaciados un dia entre ficheros
                    # distintos para que el orden en la lista de conversaciones
                    # tenga sentido cronologico relativo, aunque no sean las
                    # fechas reales.
                    start = now - timedelta(days=(len(files) - offset))
                    import_conversation(cur, user_id, conv, start)
                    conn.commit()
                    imported_count += 1
                    shutil.move(path, os.path.join(DONE_DIR, os.path.basename(path)))
                else:
                    conn.rollback()

        logger.info(f"Listo. Ficheros importados: {imported_count}/{len(files)}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
