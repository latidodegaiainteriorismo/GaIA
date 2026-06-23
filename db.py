import logging
import psycopg2
import psycopg2.extras
from config import DATABASE_URL

logger = logging.getLogger(__name__)

def get_conn():
    return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)

def db_one(query, params=()):
    """Ejecuta una query y devuelve la primera fila como dict, o None."""
    try:
        conn = get_conn()
        cur  = conn.cursor()
        cur.execute(query, params)
        row  = cur.fetchone()
        conn.commit(); cur.close(); conn.close()
        return dict(row) if row else None
    except Exception as e:
        logger.error(f'[DB one] {e}')
        return None

def db_all(query, params=()):
    """Ejecuta una query y devuelve todas las filas como lista de dicts."""
    try:
        conn = get_conn()
        cur  = conn.cursor()
        cur.execute(query, params)
        rows = cur.fetchall()
        cur.close(); conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f'[DB all] {e}')
        return []

def db_run(query, params=()):
    """Ejecuta una query sin retorno (INSERT, UPDATE, DELETE)."""
    try:
        conn = get_conn()
        cur  = conn.cursor()
        cur.execute(query, params)
        conn.commit(); cur.close(); conn.close()
        return True
    except Exception as e:
        logger.error(f'[DB run] {e}')
        return False
