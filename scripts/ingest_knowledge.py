"""
Ingest de conocimiento para GaIA.
Lee todos los .txt en knowledge/{categoria}/titulo.txt
Genera embeddings multilingüe y los guarda en Supabase knowledge_chunks.
"""

import os
import re
import sys
import psycopg2
from pathlib import Path
from sentence_transformers import SentenceTransformer

DATABASE_URL = os.environ.get('DATABASE_URL')
KNOWLEDGE_DIR = Path(__file__).parent.parent / 'knowledge'
MODEL_NAME = 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'
CHUNK_SIZE  = 300   # tokens aprox (bajado de 500 para ahorrar tokens/día en Groq)
OVERLAP     = 30

# Los nombres de carpeta en disco son ASCII puro (a prueba de encodings rotos
# en git/CI/zip). Este mapa traduce el slug de carpeta al nombre "bonito" con
# tilde que se guarda como category en la base de datos.
CATEGORY_DISPLAY_NAMES = {
    'adrian-lozano':          'Adrián Lozano',
    'monica-martos':          'Mónica Martos',
    'enciclopedia-biologia':  'Enciclopedia de la Biología',
}

def resolve_category(folder_name: str) -> str:
    return CATEGORY_DISPLAY_NAMES.get(folder_name, folder_name)

# ── Conectar a Supabase ───────────────────────────────────────────────────────
def get_conn():
    return psycopg2.connect(DATABASE_URL)

# ── Chunking ──────────────────────────────────────────────────────────────────
def chunk_text(text):
    text      = re.sub(r'\n{3,}', '\n\n', text.strip())
    text      = re.sub(r'[ \t]+', ' ', text)
    words     = text.split()
    chunks    = []
    size_w    = int(CHUNK_SIZE * 0.75)
    overlap_w = int(OVERLAP    * 0.75)
    start     = 0
    while start < len(words):
        end   = min(start + size_w, len(words))
        chunk = ' '.join(words[start:end])
        if chunk.strip():
            chunks.append(chunk.strip())
        start += size_w - overlap_w
    return chunks

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    if not DATABASE_URL:
        print('❌ DATABASE_URL no definida')
        sys.exit(1)

    # Recopilar archivos TXT
    txt_files = list(KNOWLEDGE_DIR.rglob('*.txt'))
    if not txt_files:
        print('⚠️  No hay archivos .txt en knowledge/')
        return

    print(f'📚 {len(txt_files)} archivos encontrados')
    print(f'🤖 Cargando modelo {MODEL_NAME}...')
    model = SentenceTransformer(MODEL_NAME)
    print('✅ Modelo cargado')

    conn = get_conn()
    cur  = conn.cursor()
    total_saved = 0

    for filepath in sorted(txt_files):
        # Categoría = nombre de la carpeta padre, traducido al nombre bonito
        # a través de CATEGORY_DISPLAY_NAMES (o 'otro' si no hay carpeta)
        raw_folder = filepath.parent.name if filepath.parent != KNOWLEDGE_DIR else 'otro'
        category   = resolve_category(raw_folder)
        # Fuente = nombre del archivo sin extensión
        source   = filepath.stem
        source_label = f'{source} ({category})'

        print(f'\n📄 {source_label}')

        # Opción A: Ignorar caracteres con errores de codificación (Solución rápida)
text = filepath.read_text(encoding='utf-8', errors='ignore').strip()

# Opción B: Probar utf-8 y hacer fallback a latin-1 si falla (Más robusta)
try:
    text = filepath.read_text(encoding='utf-8').strip()
except UnicodeDecodeError:
    text = filepath.read_text(encoding='latin-1').strip()      
        text = filepath.read_text(encoding='utf-8').strip()
        if not text:
            print('   ⚠️  Vacío, saltando')
            continue

        chunks = chunk_text(text)
        print(f'   ✂️  {len(chunks)} chunks')

        # Borrar chunks anteriores de esta fuente (idempotente)
        cur.execute('DELETE FROM knowledge_chunks WHERE source = %s', (source_label,))
        deleted = cur.rowcount
        if deleted:
            print(f'   🗑️  {deleted} chunks anteriores eliminados')

        # Embeddings en batch (más rápido)
        embeddings = model.encode(chunks, batch_size=32, show_progress_bar=False)

        for chunk, embedding in zip(chunks, embeddings):
            cur.execute(
                """
                INSERT INTO knowledge_chunks (source, category, content, embedding)
                VALUES (%s, %s, %s, %s::vector)
                """,
                (source_label, category, chunk, str(embedding.tolist()))
            )

        conn.commit()
        total_saved += len(chunks)
        print(f'   ✅ {len(chunks)} chunks guardados')

    cur.close()
    conn.close()
    print(f'\n🎉 Total: {total_saved} chunks en Supabase')

if __name__ == '__main__':
    main()
