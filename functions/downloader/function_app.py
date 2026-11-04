"""Baja el archivo diario de OpenCellID y avisa al procesador por una cola."""
import json
import logging
import os
import tempfile
from datetime import datetime, timezone

import azure.functions as func
import requests
from azure.storage.blob import BlobServiceClient
from azure.storage.queue import QueueClient

app = func.FunctionApp()


def download_daily():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    blob_name = f"{today}/732.csv.gz"
    storage = os.environ["DATA_STORAGE_CONNECTION"]
    blob = BlobServiceClient.from_connection_string(storage).get_blob_client("raw", blob_name)

    # Cada archivo solo se puede bajar dos veces al día: no repetir si ya existe.
    if blob.exists():
        logging.info("%s ya existe; no se descarga otra vez", blob_name)
        return blob_name

    with tempfile.TemporaryFile() as tmp:
        with requests.get(os.environ["OPENCELLID_DOWNLOAD_URL"], stream=True, timeout=120) as response:
            response.raise_for_status()
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                tmp.write(chunk)
        tmp.seek(0)
        # Un .gz empieza con 1F 8B. Si no, el sitio devolvió un mensaje de error.
        if tmp.read(2) != b"\x1f\x8b":
            raise ValueError("La respuesta no es un archivo .gz")
        tmp.seek(0)
        blob.upload_blob(tmp)

    queue = QueueClient.from_connection_string(storage, "ingest-jobs")
    queue.send_message(json.dumps({"blob": blob_name}))
    logging.info("Archivo guardado y mensaje enviado: %s", blob_name)
    return blob_name


# 03:00 UTC son las 22:00 en Colombia. Los archivos se generan antes de las 02:00 GMT.
@app.timer_trigger(schedule="0 0 3 * * *", arg_name="timer")
def daily_download(timer: func.TimerRequest) -> None:
    download_daily()
