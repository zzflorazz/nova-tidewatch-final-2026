"""Serve C02 and Trace together, with relative links across static mounts."""

from html.parser import HTMLParser
from urllib.parse import urljoin

import pytest
from fastapi.testclient import TestClient

from backend.api import create_app


@pytest.fixture
def client():
    with TestClient(create_app(":memory:")) as instance:
        yield instance


class Links(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.hrefs = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.hrefs.append(href)


def assert_relative_entry(text, page_path, expected_path):
    # Check the resolved destination, not only a path mentioned in page text.
    base = "http://testserver" + page_path
    entries = [href for href in Links(text).hrefs
               if urljoin(base, href) == "http://testserver" + expected_path]
    assert entries, f"Missing navigation from {page_path} to {expected_path}"
    assert all(not href.startswith(("/", "http:", "https:")) for href in entries)


def test_monitor_index_served(client):
    response = client.get("/monitor/index.html")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "本页展示实时监测与规则告警" in response.text


def test_monitor_3d_served(client):
    response = client.get("/monitor/3d.html")
    assert response.status_code == 200
    assert "TideWatch 3D 数字空间" in response.text


def test_workbench_links_to_monitor(client):
    response = client.get("/workbench/")
    assert response.status_code == 200
    assert_relative_entry(response.text, "/workbench/", "/monitor/index.html")
    assert "本页基于告警后的断面数据做候选来源分析" in response.text


@pytest.mark.parametrize("page", ["index.html", "3d.html", "multimodal.html"])
def test_monitor_links_to_workbench(client, page):
    response = client.get(f"/monitor/{page}")
    assert response.status_code == 200
    assert_relative_entry(response.text, f"/monitor/{page}", "/workbench/")
    assert "异常溯源工作台" in response.text


def test_api_health_unchanged(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    health = response.json()
    assert health["status"] == "ok"
    assert health["data_classification"] == "SIMULATED"
    assert health["database"]["status"] == "ok"
    assert health["model"]["status"] == "loaded"



def test_monitor_multimodal_served(client):
    response = client.get("/monitor/multimodal.html")
    assert response.status_code == 200
    assert "TideWatch 多模态交互" in response.text


@pytest.mark.parametrize("asset,minimum_size", [
    ("three.min.js", 500000), ("OrbitControls.js", 20000), ("mqtt.min.js", 100000),
])
def test_vendor_assets_served(client, asset, minimum_size):
    response = client.get(f"/monitor/vendor/{asset}")
    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]
    assert int(response.headers["content-length"]) == len(response.content) > minimum_size
    assert not response.text.lstrip().lower().startswith("<!doctype html")


@pytest.mark.parametrize("page", ["index.html", "3d.html", "multimodal.html"])
def test_monitor_scripts_are_local(client, page):
    class Scripts(HTMLParser):
        def __init__(self):
            super().__init__()
            self.sources = []

        def handle_starttag(self, tag, attrs):
            if tag == "script" and dict(attrs).get("src"):
                self.sources.append(dict(attrs)["src"])

    parser = Scripts()
    parser.feed(client.get(f"/monitor/{page}").text)
    assert "vendor/mqtt.min.js" in parser.sources
    if page == "3d.html":
        assert "vendor/three.min.js" in parser.sources
        assert "vendor/OrbitControls.js" in parser.sources
    for source in parser.sources:
        assert not source.startswith(("http:", "https:", "//", "/"))
        path = urljoin(f"/monitor/{page}", source)
        assert client.get(path).status_code == 200


def test_mqtt_failure_notice_is_explicit(client):
    script = client.get("/monitor/js/mqtt_client.js").text
    assert "未连接实时数据源，当前展示为离线静态视图" in script
    assert "_offlineNotice(true)" in script
    assert "_offlineNotice(false)" in script
