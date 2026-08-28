import os
import threading
import time
import logging

import psycopg2
from flask import Flask, jsonify, request

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

APP_VERSION = os.environ.get("APP_VERSION", "dev")
PORT = int(os.environ.get("PORT", 3000))

db_ready = False


def get_conn():
    return psycopg2.connect(
        host=os.environ.get("PGHOST"),
        port=os.environ.get("PGPORT", 5432),
        user=os.environ.get("PGUSER"),
        password=os.environ.get("PGPASSWORD"),
        dbname=os.environ.get("PGDATABASE"),
        connect_timeout=2,
    )


def init_db(retries=10, delay_seconds=3):
    global db_ready
    for attempt in range(1, retries + 1):
        try:
            conn = get_conn()
            with conn, conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS items (
                        id SERIAL PRIMARY KEY,
                        name TEXT NOT NULL,
                        created_at TIMESTAMPTZ DEFAULT now()
                    );
                    """
                )
            conn.close()
            db_ready = True
            log.info("DB schema ready")
            return
        except Exception as err:
            log.error("DB init attempt %d/%d failed: %s", attempt, retries, err)
            if attempt == retries:
                return
            time.sleep(delay_seconds)


app = Flask(__name__)
threading.Thread(target=init_db, daemon=True).start()


# Liveness: is the process itself alive? Deliberately does NOT touch the
# database - a slow/down DB should not convince Kubernetes to kill and
# restart an otherwise-healthy process.
@app.get("/healthz/live")
def healthz_live():
    return jsonify(status="alive", version=APP_VERSION), 200


# Readiness: can this pod actually serve traffic right now? Touches the
# database on every call, so Kubernetes pulls this pod out of the Service's
# endpoints immediately if the DB is unreachable.
@app.get("/healthz/ready")
def healthz_ready():
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        conn.close()
        return jsonify(status="ready", db="connected"), 200
    except Exception as err:
        return jsonify(status="not-ready", db="unreachable", error=str(err)), 503


@app.get("/")
def index():
    return jsonify(service="devops-challenge-backend", version=APP_VERSION, dbReady=db_ready)


@app.get("/items")
def list_items():
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, created_at FROM items ORDER BY id DESC LIMIT 50")
            rows = cur.fetchall()
        conn.close()
        return jsonify(
            [{"id": r[0], "name": r[1], "created_at": r[2].isoformat()} for r in rows]
        )
    except Exception as err:
        return jsonify(error=str(err)), 500


@app.get("/items/<int:item_id>")
def get_item(item_id):
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, created_at FROM items WHERE id = %s", (item_id,)
            )
            row = cur.fetchone()
        conn.close()
        if row is None:
            return jsonify(error="item not found"), 404
        return jsonify(id=row[0], name=row[1], created_at=row[2].isoformat())
    except Exception as err:
        return jsonify(error=str(err)), 500


@app.post("/items")
def create_item():
    body = request.get_json(silent=True) or {}
    name = body.get("name")
    if not name:
        return jsonify(error="name is required"), 400
    try:
        conn = get_conn()
        with conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO items (name) VALUES (%s) RETURNING id, name, created_at",
                (name,),
            )
            row = cur.fetchone()
        conn.close()
        return jsonify(id=row[0], name=row[1], created_at=row[2].isoformat()), 201
    except Exception as err:
        return jsonify(error=str(err)), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
