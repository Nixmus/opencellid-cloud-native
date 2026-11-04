"""Pruebas de la API. Necesitan DATABASE_URL con el esquema de db/init.sql creado."""
import os

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("DELETE FROM cells WHERE mcc = 999")
        conn.execute("""
            INSERT INTO cells (radio, mcc, net, area, cell, lon, lat, range_m, samples,
                               created_at, updated_at, geom)
            VALUES
              ('LTE', 999, 1, 10, 100, -74.08, 4.60, 1000, 5, now(), now(),
               ST_SetSRID(ST_MakePoint(-74.08, 4.60), 4326)::geography),
              ('LTE', 999, 1, 10, 101, -74.07, 4.61, 1000, 5, now(), now(),
               ST_SetSRID(ST_MakePoint(-74.07, 4.61), 4326)::geography)""")
    with TestClient(app) as test_client:
        yield test_client
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("DELETE FROM cells WHERE mcc = 999")


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_ready(client):
    assert client.get("/ready").status_code == 200


def test_get_cell_has_attribution(client):
    body = client.get("/v1/cells/LTE/999/1/10/100").json()
    assert body["cell"]["lat"] == 4.60
    assert body["attribution"]["source"] == "https://opencellid.org"


def test_unknown_cell_returns_404(client):
    assert client.get("/v1/cells/LTE/999/1/10/1").status_code == 404


def test_nearby(client):
    body = client.get("/v1/cells/nearby", params={"lat": 4.60, "lon": -74.08, "radius_m": 500}).json()
    assert body["count"] >= 1


def test_operators(client):
    assert client.get("/v1/operators", params={"mcc": 999}).json()["operators"][0]["cells"] == 2


def test_locate_averages_cells(client):
    cells = [{"radio": "LTE", "mcc": 999, "net": 1, "area": 10, "cell": c} for c in (100, 101)]
    body = client.post("/v1/locate", json={"cells": cells}).json()
    assert body["lat"] == pytest.approx(4.605)
    assert body["cells_used"] == 2
    assert body["estimated"] is True


def test_locate_rejects_invalid_radio(client):
    cells = [{"radio": "WIFI", "mcc": 999, "net": 1, "area": 10, "cell": 100}]
    assert client.post("/v1/locate", json={"cells": cells}).status_code == 422
