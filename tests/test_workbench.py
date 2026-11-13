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
    health = response.json()
    assert health["status"] == "ok"
    assert health["data_classification"] == "SIMULATED"
    assert health["database"]["status"] == "ok"
    assert health["model"]["status"] == "loaded"


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


def test_scenario_display_metadata(client):
    scenarios = client.get("/api/scenarios").json()
    assert [s["display_name"] for s in scenarios] == [
        "基线平稳", "单源明确", "多源候选", "同现象·上游来源",
        "同现象·本地来源", "关键上游失联", "自建对照·幅度干扰",
    ]
    for scenario in scenarios:
        assert scenario["display_name"].strip()
        assert scenario["display_note"].strip()
        detail = client.get(f"/api/scenarios/{scenario['scenario_id']}").json()
        assert detail["display_name"] == scenario["display_name"]
        assert detail["display_note"] == scenario["display_note"]


def test_workbench_uses_display_names_not_stage_numbers(client):
    js = client.get("/workbench/app.js").text
    html = client.get("/workbench/").text
    # Keep IDs for API requests/data attributes, never render them as titles.
    assert "${escapeHtml(s.display_name)}" in js
    assert "${escapeHtml(s.display_note)}" in js
    assert 'el("selection-label").textContent = s.display_name;' in js
    assert "正在运行 ${s.display_name}" in js
    assert "${escapeHtml(s.scenario_id)}</span>" not in js
    for stage in ("S1", "S2", "S3", "S4", "S5", "D1"):
        assert stage not in js
        assert stage not in html


RESULT_REGIONS = [
    ("overview", "总览", "Overview"),
    ("active-anomaly", "活动异常", "Active Anomaly"),
    ("attribution-board", "归因看板", "Attribution Board"),
    ("evidence-chain", "证据链", "Evidence Chain"),
    ("next-check", "下一步排查", "Next Check"),
]


def test_result_regions_and_anchor_navigation(client):
    html = client.get("/workbench/").text
    for region, chinese, english in RESULT_REGIONS:
        assert f'id="{region}"' in html
        assert f'href="#{region}"' in html
        assert f'{chinese} <span>{english}</span>' in html
        assert f'id="{region}-content"' in html


def test_overview_svg_renderer(client):
    js = client.get("/workbench/app.js").text
    assert "function renderOverviewTopology(output, record)" in js
    assert '<svg class="overview-topology"' in js
    assert "record?.input_snapshot?.current_samples" in js
    assert "本次输出未提供" in js
    assert "<th>幅度衰减</th>" in js


def test_result_region_order_and_version_history_location(client):
    html = client.get("/workbench/").text
    positions = [html.index(f'<section id="{region}"') for region, _, _ in RESULT_REGIONS]
    assert positions == sorted(positions)
    # History remains in the stable Evidence Chain container, not replaced
    # during result rendering; its existing event listener keeps working.
    assert positions[3] < html.index('id="version-history"') < positions[4]
    js = client.get("/workbench/app.js").text
    assert 'el("result-content").replaceChildren()' not in js
    assert 'el("next-check-content").innerHTML = renderNextChecks(output)' in js
