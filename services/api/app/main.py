"""API de celdas. Solo lee de PostgreSQL: nunca llama a OpenCellID en línea."""
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Response
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, Field

ATTRIBUTION = {
    "text": "Cell tower data from OpenCellID, licensed under CC BY-SA 4.0.",
    "source": "https://opencellid.org",
    "license": "https://creativecommons.org/licenses/by-sa/4.0/",
}
CELL_FIELDS = "radio, mcc, net, area, cell, lon, lat, range_m, samples, updated_at"

pool = AsyncConnectionPool(
    conninfo=os.environ.get("DATABASE_URL", ""), open=False, min_size=1, max_size=5,
    kwargs={"row_factory": dict_row},
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await pool.open()
    yield
    await pool.close()


app = FastAPI(title="Cell API", version="1.0.0", lifespan=lifespan,
              description=f"{ATTRIBUTION['text']} Las ubicaciones son estimadas.")


class Cell(BaseModel):
    radio: str = Field(pattern="^(GSM|UMTS|CDMA|LTE|NR)$")
    mcc: int
    net: int
    area: int
    cell: int


class LocateRequest(BaseModel):
    cells: list[Cell] = Field(min_length=1, max_length=20)


async def query(sql, params=()):
    async with pool.connection() as conn:
        cursor = await conn.execute(sql, params)
        return await cursor.fetchall()


@app.get("/health")
async def health():
    """El proceso está vivo. No toca la base de datos."""
    return {"status": "ok"}


@app.get("/ready")
async def ready(response: Response):
    """Listo para recibir tráfico solo si la base de datos responde."""
    try:
        await query("SELECT 1 AS ok")
        return {"status": "ready"}
    except Exception:
        response.status_code = 503
        return {"status": "database unavailable"}


@app.get("/v1/cells/nearby")
async def cells_nearby(
    lat: float = Query(ge=-90, le=90),
    lon: float = Query(ge=-180, le=180),
    radius_m: int = Query(default=2000, ge=1, le=20000),
    limit: int = Query(default=200, ge=1, le=1000),
):
    rows = await query(
        f"""SELECT {CELL_FIELDS} FROM cells
            WHERE ST_DWithin(geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s)
            LIMIT %s""",
        (lon, lat, radius_m, limit),
    )
    return {"count": len(rows), "cells": rows, "attribution": ATTRIBUTION}


@app.get("/v1/cells/{radio}/{mcc}/{net}/{area}/{cell}")
async def get_cell(radio: str, mcc: int, net: int, area: int, cell: int):
    rows = await query(
        f"""SELECT {CELL_FIELDS} FROM cells
            WHERE radio = %s AND mcc = %s AND net = %s AND area = %s AND cell = %s""",
        (radio.upper(), mcc, net, area, cell),
    )
    if not rows:
        raise HTTPException(404, "Celda no encontrada")
    return {"cell": rows[0], "attribution": ATTRIBUTION}


@app.get("/v1/operators")
async def operators(mcc: int = 732):
    rows = await query(
        """SELECT net, radio, count(*) AS cells FROM cells
           WHERE mcc = %s GROUP BY net, radio ORDER BY net, radio""",
        (mcc,),
    )
    return {"mcc": mcc, "operators": rows, "attribution": ATTRIBUTION}


@app.post("/v1/locate")
async def locate(body: LocateRequest):
    """Estima dónde está un dispositivo con las celdas que detecta, sin GPS."""
    found = []
    for item in body.cells:
        found += await query(
            """SELECT lat, lon, range_m FROM cells
               WHERE radio = %s AND mcc = %s AND net = %s AND area = %s AND cell = %s""",
            (item.radio, item.mcc, item.net, item.area, item.cell),
        )
    if not found:
        raise HTTPException(404, "Ninguna celda está en la base de datos")
    # Una celda de menor alcance aporta una posición más precisa.
    weights = [1 / max(row["range_m"], 100) for row in found]
    total = sum(weights)
    return {
        "lat": sum(r["lat"] * w for r, w in zip(found, weights)) / total,
        "lon": sum(r["lon"] * w for r, w in zip(found, weights)) / total,
        "accuracy_m": min(row["range_m"] for row in found),
        "cells_used": len(found),
        "estimated": True,
        "attribution": ATTRIBUTION,
    }
