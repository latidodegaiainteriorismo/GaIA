import logging
import threading
import psycopg2
import psycopg2.extras
import psycopg2.pool
from config import DATABASE_URL

logger = logging.getLogger(__name__)

# Pool de conexiones: antes cada consulta abría una conexión nueva a Supabase
# (TLS + autenticación, centenares de ms) y un solo mensaje del chat hace
# decenas de consultas. Se reutilizan conexiones entre consultas y entre hilos
# (gunicorn corre con --threads, ver Procfile).
_POOL_MIN = 1
_POOL_MAX = 8

_pool = None
_pool_lock = threading.Lock()


def _get_pool():
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = psycopg2.pool.ThreadedConnectionPool(
                    _POOL_MIN, _POOL_MAX, DATABASE_URL,
                    cursor_factory=psycopg2.extras.RealDictCursor,
                )
    return _pool


def get_conn():
    """Conexión suelta fuera del pool (la usan scripts; hay que cerrarla)."""
    return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)


def _acquire():
    """Devuelve (conexión, del_pool). Si el pool está agotado, abre una suelta."""
    try:
        return _get_pool().getconn(), True
    except psycopg2.pool.PoolError:
        logger.warning('[DB] Pool agotado — conexión temporal fuera del pool')
        return get_conn(), False


def _release(conn, pooled, bad=False):
    """Devuelve la conexión al pool dejándola limpia, o la descarta si está rota."""
    try:
        if not bad and not conn.closed:
            conn.rollback()  # cierra cualquier transacción abierta antes de reutilizarla
            if conn.autocommit:  # una lectura que falló puede dejarla en autocommit
                conn.autocommit = False
    except Exception:
        bad = True
    try:
        if pooled:
            _get_pool().putconn(conn, close=bad or bool(conn.closed))
        else:
            conn.close()
    except Exception as e:
        logger.warning(f'[DB] No se pudo devolver la conexión al pool: {e}')


def _execute(query, params, mode):
    """
    Ejecuta una consulta. mode: 'one' | 'all' | 'run'.
    'all' no confirma (igual que antes: es solo lectura); 'one' y 'run' sí.
    Devuelve (ok, resultado).

    Supabase cierra por el servidor las conexiones inactivas: si la conexión
    del pool estaba rota, se descarta y se reintenta UNA vez con una nueva.
    El reintento solo se hace si el fallo ocurre antes del commit, para no
    repetir una escritura que quizá sí se confirmó.
    """
    for attempt in (1, 2):
        conn, pooled = _acquire()
        committing = False
        try:
            # Las lecturas van en autocommit: se ahorran los viajes de red de
            # BEGIN y ROLLBACK (db_all nunca se usa para escribir).
            if mode == 'all':
                conn.autocommit = True
            cur = conn.cursor()
            cur.execute(query, params)
            if mode == 'one':
                row = cur.fetchone()
                result = dict(row) if row else None
            elif mode == 'all':
                result = [dict(r) for r in cur.fetchall()]
            else:
                result = True
            cur.close()
            if mode == 'all':
                conn.autocommit = False
            else:
                committing = True
                conn.commit()
            _release(conn, pooled)
            return True, result
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
            _release(conn, pooled, bad=True)
            if attempt == 1 and not committing:
                logger.warning(f'[DB] Conexión rota ({type(e).__name__}) — reintentando con una nueva')
                continue
            logger.error(f'[DB {mode}] {e}')
            return False, None
        except Exception as e:
            _release(conn, pooled)
            logger.error(f'[DB {mode}] {e}')
            return False, None
    return False, None


def db_one(query, params=()):
    """Ejecuta una query y devuelve la primera fila como dict, o None."""
    _, result = _execute(query, params, 'one')
    return result


def db_all(query, params=()):
    """Ejecuta una query y devuelve todas las filas como lista de dicts."""
    _, result = _execute(query, params, 'all')
    return result if result is not None else []


def db_run(query, params=()):
    """Ejecuta una query sin retorno (INSERT, UPDATE, DELETE)."""
    ok, _ = _execute(query, params, 'run')
    return ok
