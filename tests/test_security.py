"""Cross-origin policy: a page on another site must not be able to change
the library through the user's browser.

Browsers send the Origin header on every POST/PUT/PATCH/DELETE, including
the ones they send without asking the server first. The API uses that to
refuse writes that come from somewhere else, and only hands CORS headers
to origins that are allowed - so a page on another site can neither read
anything from the server nor change the library.
"""
import pytest

from newsreader.config import settings

OTHER = "https://evil.example"
READER = "https://reader.example"


@pytest.fixture()
def origins(monkeypatch):
    """Set the allowed origins."""
    monkeypatch.setattr(settings, "nr_allowed_origins", "")
    return lambda value: monkeypatch.setattr(settings, "nr_allowed_origins", value)


def test_write_from_another_site_is_refused(client):
    cat = client.post("/api/categories", json={"name": "Research"}).json()
    r = client.post(f"/api/categories/{cat['id']}/x", headers={"Origin": OTHER})
    assert r.status_code == 403
    r = client.post("/api/categories", json={"name": "Injected"},
                    headers={"Origin": OTHER})
    assert r.status_code == 403
    assert [c["name"] for c in client.get("/api/categories").json()["categories"]] \
        == ["Research"]


def test_delete_from_another_site_is_refused(client):
    cat = client.post("/api/categories", json={"name": "Research"}).json()
    r = client.delete(f"/api/categories/{cat['id']}", headers={"Origin": OTHER})
    assert r.status_code == 403
    assert len(client.get("/api/categories").json()["categories"]) == 1


def test_read_from_another_site_works_but_cannot_be_read_back(client):
    """The request goes through, but no CORS header means the page cannot
    read the answer - and reads change nothing anyway."""
    r = client.get("/api/health", headers={"Origin": OTHER})
    assert r.status_code == 200
    assert "access-control-allow-origin" not in r.headers


def test_preflight_from_another_site_is_refused(client):
    r = client.options("/api/categories", headers={
        "Origin": OTHER,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    })
    assert r.status_code == 403
    assert "access-control-allow-origin" not in r.headers


def test_allowed_origin_gets_cors_headers(client, origins):
    origins(READER)
    r = client.get("/api/health", headers={"Origin": READER})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == READER
    assert r.headers["vary"] == "Origin"


def test_allowed_origin_preflight_is_answered(client, origins):
    origins(f"{READER}, https://other.example")
    r = client.options("/api/categories", headers={
        "Origin": "https://other.example",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    })
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "https://other.example"
    assert "POST" in r.headers["access-control-allow-methods"]


def test_allowed_origin_may_write(client, origins):
    origins(READER)
    r = client.post("/api/categories", json={"name": "From the reader"},
                    headers={"Origin": READER})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == READER


def test_origin_outside_the_list_is_still_refused(client, origins):
    origins(READER)
    r = client.post("/api/categories", json={"name": "Injected"},
                    headers={"Origin": OTHER})
    assert r.status_code == 403
    r = client.get("/api/health", headers={"Origin": OTHER})
    assert "access-control-allow-origin" not in r.headers


def test_the_servers_own_page_may_write(client):
    """The bundled web client is served by this server, so its Origin names
    the host the request was addressed to."""
    r = client.post("/api/categories", json={"name": "Local"},
                    headers={"Origin": "http://testserver"})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "http://testserver"


def test_wildcard_allows_everything_on_purpose(client, origins):
    origins("*")
    r = client.get("/api/health", headers={"Origin": OTHER})
    assert r.headers["access-control-allow-origin"] == "*"
    r = client.post("/api/categories", json={"name": "Anyone"},
                    headers={"Origin": OTHER})
    assert r.status_code == 200


def test_requests_without_an_origin_are_left_alone(client):
    """Not a browser: curl, scripts, the test suite."""
    r = client.post("/api/categories", json={"name": "Scripted"})
    assert r.status_code == 200
    r = client.delete(f"/api/categories/{r.json()['id']}")
    assert r.status_code == 200
