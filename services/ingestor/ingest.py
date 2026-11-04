"""Carga un CSV de OpenCellID en PostgreSQL.

Modo Azure: lee un mensaje de la cola, baja el archivo de Blob Storage y lo carga.
Modo local: python ingest.py ruta/al/archivo.csv.gz
"""
import csv
import gzip
import json
import logging
import os
import sys
import tempfile
from datetime import datetime, timezone

import psycopg

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("ingestor")

COLUMNS = ["radio", "mcc", "net", "area", "cell", "unit", "lon", "lat", "range",
           "samples", "changeable", "created", "updated", "averageSignal"]
VALID_RADIOS = {"GSM", "UMTS", "CDMA", "LTE", "NR"}
MAX_ATTEMPTS = 3


def parse_row(row):
    """Convierte una fila a tupla. Devuelve None si la fila no sirve."""
    if len(row) < len(COLUMNS):
        return None
    d = dict(zip(COLUMNS, row))
    try:
        radio = d["radio"].strip().upper()
        lon, lat = float(d["lon"]), float(d["lat"])
        if radio not in VALID_RADIOS or not (-180 <= lon <= 180 and -90 <= lat <= 90):
            return None
        return (
            radio, int(d["mcc"]), int(d["net"]), int(d["area"]), int(d["cell"]),
            int(d["unit"]) if d["unit"].strip() else None,
            lon, lat, int(d["range"]), int(d["samples"]),
            datetime.fromtimestamp(int(d["created"]), timezone.utc),
            datetime.fromtimestamp(int(d["updated"]), timezone.utc),
        )
    except (ValueError, OverflowError):
        return None


def read_rows(path):
    """Lee .csv o .csv.gz. Acepta archivos con o sin encabezado."""
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", newline="", encoding="utf-8") as handle:
        for row in csv.reader(handle):
            if row and row[0].strip().lower() == "radio":
                continue
            yield row


def load_file(path, database_url):
    """Carga todo en una transacción: si algo falla, no queda nada a medias."""
    loaded = rejected = 0
    with psycopg.connect(database_url) as conn, conn.cursor() as cur:
        cur.execute("""
            CREATE TEMP TABLE stage (
                radio text, mcc int, net int, area int, cell bigint, unit int,
                lon double precision, lat double precision, range_m int, samples int,
                created_at timestamptz, updated_at timestamptz
            ) ON COMMIT DROP""")
        with cur.copy("COPY stage FROM STDIN") as copy:
            for row in read_rows(path):
                parsed = parse_row(row)
                if parsed is None:
                    rejected += 1
                    continue
                copy.write_row(parsed)
                loaded += 1
        # Una celda puede repetirse: se conserva la que tiene más mediciones.
        cur.execute("""
            INSERT INTO cells (radio, mcc, net, area, cell, unit, lon, lat, range_m,
                               samples, created_at, updated_at, geom)
            SELECT DISTINCT ON (radio, mcc, net, area, cell)
                   radio, mcc, net, area, cell, unit, lon, lat, range_m,
                   samples, created_at, updated_at,
                   ST_SetSRID(ST_MakePoint(lon, lat), 4326)::geography
            FROM stage
            ORDER BY radio, mcc, net, area, cell, samples DESC
            ON CONFLICT (radio, mcc, net, area, cell) DO UPDATE SET
                unit = EXCLUDED.unit, lon = EXCLUDED.lon, lat = EXCLUDED.lat,
                range_m = EXCLUDED.range_m, samples = EXCLUDED.samples,
                updated_at = EXCLUDED.updated_at, geom = EXCLUDED.geom""")
    log.info("cargadas=%s rechazadas=%s", loaded, rejected)
    return loaded, rejected


def run_from_queue():
    from azure.storage.blob import BlobServiceClient
    from azure.storage.queue import QueueClient

    storage = os.environ["STORAGE_CONNECTION"]
    queue = QueueClient.from_connection_string(storage, os.environ.get("QUEUE_NAME", "ingest-jobs"))
    # El mensaje queda oculto 30 min. Se borra solo si la carga termina bien.
    messages = queue.receive_messages(max_messages=1, visibility_timeout=1800)
    message = next(iter(messages), None)
    if message is None:
        log.info("No hay mensajes en la cola")
        return

    if message.dequeue_count > MAX_ATTEMPTS:
        log.error("Mensaje descartado tras %s intentos: %s", MAX_ATTEMPTS, message.content)
        queue.delete_message(message)
        return

    blob_name = json.loads(message.content)["blob"]
    blob = BlobServiceClient.from_connection_string(storage).get_blob_client(
        os.environ.get("BLOB_CONTAINER", "raw"), blob_name)
    with tempfile.NamedTemporaryFile(suffix=".csv.gz") as tmp:
        blob.download_blob().readinto(tmp)
        tmp.flush()
        load_file(tmp.name, os.environ["DATABASE_URL"])
    queue.delete_message(message)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        load_file(sys.argv[1], os.environ["DATABASE_URL"])
    else:
        run_from_queue()
