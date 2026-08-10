"""Read/write helper for the separately deployed dashboard app."""
import base64, os
from contextlib import contextmanager
import psycopg2
from databricks.sdk import WorkspaceClient
from psycopg2.extras import RealDictCursor

def url():
    if os.getenv("LAKEBASE_URL"): return os.environ["LAKEBASE_URL"]
    value = WorkspaceClient().secrets.get_secret(scope=os.getenv("LAKEBASE_SECRET_SCOPE", "database"), key=os.getenv("LAKEBASE_SECRET_KEY", "lakebase-url"))
    return base64.b64decode(value.value).decode()

@contextmanager
def connection():
    conn = psycopg2.connect(url(), cursor_factory=RealDictCursor)
    try: yield conn
    finally: conn.close()

def query(sql, params=None):
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params); return [dict(row) for row in cur.fetchall()]

def write(sql, params=None, returning=False):
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params); value=dict(cur.fetchone()) if returning else cur.rowcount; conn.commit(); return value
