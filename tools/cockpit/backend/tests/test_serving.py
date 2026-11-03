import pytest
from django.db import connection
from django.test import Client

pytestmark = pytest.mark.django_db


@pytest.fixture
def out(settings, tmp_path):
    root = tmp_path / "out"
    (root / "login").mkdir(parents=True)
    (root / "_next/static").mkdir(parents=True)
    (root / "index.html").write_text("home")
    (root / "login/index.html").write_text("login")
    (root / "404.html").write_text("missing")
    (root / "_next/static/app.js").write_text("js")
    (tmp_path / "secret.txt").write_text("no")
    settings.FRONTEND_DIR = root
    return root


def test_serves_pages_assets_and_404(out):
    c = Client()
    assert c.get("/").getvalue() == b"home"
    assert c.get("/login/").getvalue() == b"login"
    redirect = c.get("/login")
    assert redirect.status_code == 302 and redirect["Location"] == "/login/"
    js = c.get("/_next/static/app.js")
    assert js["Content-Type"].startswith("text/javascript")
    assert "immutable" in js["Cache-Control"]
    missing = c.get("/nope/")
    assert missing.status_code == 404 and missing.getvalue() == b"missing"


def test_no_path_traversal(out):
    assert Client().get("/../secret.txt").status_code == 404
    assert Client().get("/%2e%2e/secret.txt").status_code == 404


def test_unbuilt_frontend_explains_itself(settings, tmp_path):
    settings.FRONTEND_DIR = tmp_path / "missing"
    res = Client().get("/")
    assert res.status_code == 503 and b"npm run build" in res.content


def test_login_meshes_are_public_but_restricted():
    c = Client()
    ok = c.get("/login-assets/meshes/annin_ar4_description/meshes/ar4/link_1.STL")
    assert ok.status_code == 200
    assert c.get("/login-assets/meshes/annin_ar4_description/package.xml").status_code == 404
    assert c.get("/login-assets/meshes/other/meshes/x.stl").status_code == 404
    assert c.get("/login-assets/meshes/ar3_description/../../../.env").status_code == 404


def test_api_errors_are_problem_json():
    res = Client().get("/api/users")
    assert res.json()["type"] == "about:blank"


def test_timescaledb_and_pgvector_are_installed():
    with connection.cursor() as cur:
        cur.execute("SELECT extname FROM pg_extension")
        names = {row[0] for row in cur.fetchall()}
    assert {"timescaledb", "vector"} <= names


def test_openapi_schema_lists_the_api(client_in):
    res = client_in.get("/api/schema")
    assert res.status_code == 200
    text = res.content.decode()
    for path in ("/api/auth/login", "/api/setup-account", "/api/users/{user_id}", "/api/env/state"):
        assert path in text
