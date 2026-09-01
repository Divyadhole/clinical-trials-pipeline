"""Database access. One connection helper, one migration runner, one run logger."""

import logging
import pathlib
from contextlib import contextmanager

import psycopg2
import psycopg2.extras

LOG = logging.getLogger(__name__)

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent.parent / "sql" / "migrations"


@contextmanager
def connect(database_url: str):
    """Yield a connection that commits on clean exit and rolls back on error."""
    conn = psycopg2.connect(database_url)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def migrate(conn) -> list:
    """Apply every migration file in order. Each file is idempotent on its own."""
    applied = []
    with conn.cursor() as cur:
        cur.execute(
            """
            create table if not exists public.schema_migrations (
                filename    text primary key,
                applied_at  timestamptz not null default now()
            )
            """
        )
        cur.execute("select filename from public.schema_migrations")
        already = {row[0] for row in cur.fetchall()}

        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in already:
                continue
            LOG.info("applying migration %s", path.name)
            cur.execute(path.read_text())
            cur.execute(
                "insert into public.schema_migrations (filename) values (%s)",
                (path.name,),
            )
            applied.append(path.name)
    conn.commit()
    return applied


def start_run(conn, job: str, window_start=None, window_end=None) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into ops.pipeline_runs (job, window_start, window_end)
            values (%s, %s, %s)
            returning run_id
            """,
            (job, window_start, window_end),
        )
        run_id = cur.fetchone()[0]
    conn.commit()
    return run_id


def finish_run(conn, run_id: int, status: str, error: str = None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            update ops.pipeline_runs
               set status = %s, error = %s, finished_at = now()
             where run_id = %s
            """,
            (status, (error or "")[:4000] or None, run_id),
        )
    conn.commit()


def record_metrics(conn, run_id: int, metrics: dict) -> None:
    if not metrics:
        return
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(
            cur,
            """
            insert into ops.run_metrics (run_id, metric, value)
            values %s
            on conflict (run_id, metric) do update set value = excluded.value
            """,
            [(run_id, k, v) for k, v in metrics.items()],
        )
    conn.commit()


def get_watermark(conn, stream: str):
    with conn.cursor() as cur:
        cur.execute("select last_value from ops.watermarks where stream = %s", (stream,))
        row = cur.fetchone()
    return row[0] if row else None


def set_watermark(conn, stream: str, value) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into ops.watermarks (stream, last_value, updated_at)
            values (%s, %s, now())
            on conflict (stream) do update
                set last_value = excluded.last_value, updated_at = now()
            """,
            (stream, value),
        )
    conn.commit()
