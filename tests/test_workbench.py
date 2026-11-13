"""Static workbench delivery without changing existing API behavior."""

import pytest
from fastapi.testclient import TestClient

from backend.api import create_app


@pytest.fixture
def client():
    with TestClient(create_app(":memory:")) as instance:
        yield instance


def test_workbench_index_served(client):
    response = client.get("/workbench/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "SIMULATED" in response.text
    assert "全部数据为教学模拟，非真实水体观测" in response.text


def test_static_assets_served(client):
    css = client.get("/workbench/style.css")
    js = client.get("/workbench/app.js")
    assert css.status_code == js.status_code == 200
    assert "text/css" in css.headers["content-type"]
    assert "javascript" in js.headers["content-type"]


def test_existing_api_not_broken(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "data_classification": "SIMULATED"}


def test_index_has_version_and_backfill_sections(client):
    html = client.get("/workbench/").text
    assert 'id="version-history"' in html
    assert 'id="version-list"' in html
    assert 'id="backfill-section"' in html
    assert 'id="backfill-form"' in html


def test_app_js_no_external_cdn(client):
    js = client.get("/workbench/app.js").text
    assert "http://" not in js
    assert "https://" not in js


def test_baseline_card_not_runnable(client):
    js = client.get("/workbench/app.js").text
    # Baseline cards must be disabled at rendering, not merely fail after POST.
    assert 'busy || !s.target ? "disabled"' in js
    assert 'aria-disabled="${busy || !s.target}"' in js
    assert 'baseline: ["基线场景"' in js
    # The click path also guards target-less scenes against programmatic calls.
    assert "if (busy || !s?.target) return;" in js
