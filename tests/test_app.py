from larder.app import COOKIE

from .conftest import PASSWORD, core_of


def actions(client):
    return [row["action"] for row in core_of(client).db.query("SELECT action FROM audit_log ORDER BY id")]


def test_fresh_install_goes_to_setup(client):
    assert client.get("/").headers["location"] == "/setup"
    assert client.get("/unlock").headers["location"] == "/setup"
    assert client.get("/setup").status_code == 200


def test_setup_rejects_weak_or_mismatched_password(client):
    assert client.post("/setup", data={"password": "short", "confirm": "short"}).status_code == 400
    mismatch = client.post("/setup", data={"password": PASSWORD, "confirm": PASSWORD + "x"})
    assert mismatch.status_code == 400
    assert not core_of(client).initialised


def test_setup_unlocks_and_cannot_be_repeated(unlocked):
    assert unlocked.get("/").status_code == 200
    again = unlocked.post("/setup", data={"password": "another password!", "confirm": "another password!"})
    assert again.headers["location"] == "/unlock"
    assert core_of(unlocked).vault.unlock(PASSWORD)


def test_session_cookie_flags(client):
    response = client.post("/setup", data={"password": PASSWORD, "confirm": PASSWORD})
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert "max-age" not in cookie and "expires" not in cookie


def test_lock_then_unlock(unlocked):
    assert unlocked.post("/lock").headers["location"].startswith("/unlock")
    assert not core_of(unlocked).unlocked
    assert unlocked.get("/").headers["location"] == "/unlock"
    assert unlocked.get("/settings").headers["location"] == "/unlock"
    assert unlocked.post("/unlock", data={"password": "wrong password!!"}).status_code == 401
    assert unlocked.post("/unlock", data={"password": PASSWORD}).status_code == 303
    assert unlocked.get("/").status_code == 200


def test_pages_need_a_valid_session_cookie(unlocked):
    unlocked.cookies.clear()
    for path in ("/", "/settings", "/audit"):
        assert unlocked.get(path).headers["location"] == "/unlock"
    assert unlocked.post("/backup").headers["location"] == "/unlock"
    unlocked.cookies.set(COOKIE, "forged")
    assert unlocked.get("/").headers["location"] == "/unlock"
    assert unlocked.post("/api/touch").status_code == 401


def test_repeated_wrong_passwords_are_throttled(unlocked, clock):
    unlocked.post("/lock")
    for _ in range(5):
        assert unlocked.post("/unlock", data={"password": "wrong password!!"}).status_code == 401
    assert unlocked.post("/unlock", data={"password": PASSWORD}).status_code == 429
    clock.advance(6)
    assert unlocked.post("/unlock", data={"password": PASSWORD}).status_code == 303
    assert actions(unlocked).count("unlock_failed") == 5


def test_auto_lock_after_inactivity(unlocked, clock):
    clock.advance(9 * 60)
    assert unlocked.get("/").status_code == 200
    clock.advance(10 * 60 + 1)
    assert unlocked.get("/").headers["location"] == "/unlock"
    assert not core_of(unlocked).unlocked
    assert core_of(unlocked).keys is None


def test_status_poll_does_not_keep_session_alive(unlocked, clock):
    for _ in range(11):
        clock.advance(60)
        status = unlocked.get("/api/status").json()
    assert status == {"initialised": True, "unlocked": False, "seconds_left": 0}


def test_touch_keeps_session_alive(unlocked, clock):
    for _ in range(15):
        clock.advance(60)
        assert unlocked.post("/api/touch").status_code == 200
    assert unlocked.get("/api/status").json()["unlocked"] is True


def test_auto_lock_setting(unlocked, clock):
    assert unlocked.post("/settings/auto-lock", data={"minutes": "0"}).status_code == 400
    assert unlocked.post("/settings/auto-lock", data={"minutes": "abc"}).status_code == 400
    assert unlocked.post("/settings/auto-lock", data={"minutes": "2"}).status_code == 303
    clock.advance(121)
    assert unlocked.get("/").headers["location"] == "/unlock"
    unlocked.post("/unlock", data={"password": PASSWORD})
    assert core_of(unlocked).auto_lock_minutes == 2


def test_change_password(unlocked):
    new = "a brand new password"
    wrong = unlocked.post("/settings/password", data={"current": "nope nope nope", "password": new, "confirm": new})
    assert wrong.status_code == 400
    short = unlocked.post("/settings/password", data={"current": PASSWORD, "password": "x", "confirm": "x"})
    assert short.status_code == 400
    ok = unlocked.post("/settings/password", data={"current": PASSWORD, "password": new, "confirm": new})
    assert ok.status_code == 303
    unlocked.post("/lock")
    assert unlocked.post("/unlock", data={"password": PASSWORD}).status_code == 401
    assert unlocked.post("/unlock", data={"password": new}).status_code == 303


def test_audit_log_records_security_events(unlocked):
    unlocked.post("/lock")
    unlocked.post("/unlock", data={"password": "wrong password!!"})
    unlocked.post("/unlock", data={"password": PASSWORD})
    assert actions(unlocked) == ["setup", "lock", "unlock_failed", "unlock"]
    page = unlocked.get("/audit")
    assert page.status_code == 200 and "unlock_failed" in page.text


def test_rejects_unexpected_host_header(client):
    assert client.get("/setup", headers={"host": "evil.example"}).status_code == 400
    assert client.get("/setup", headers={"host": "127.0.0.1:9999"}).status_code == 400
    assert client.get("/setup", headers={"host": "localhost:8765"}).status_code == 200


def test_rejects_cross_origin_posts(unlocked):
    blocked = unlocked.post("/lock", headers={"origin": "http://evil.example"})
    assert blocked.status_code == 403
    other_port = unlocked.post("/lock", headers={"origin": "http://localhost:3000"})
    assert other_port.status_code == 403
    assert core_of(unlocked).unlocked
    assert unlocked.post("/lock", headers={"origin": "http://localhost:8765"}).status_code == 303


def test_security_headers(client):
    headers = client.get("/setup").headers
    assert "default-src 'self'" in headers["content-security-policy"]
    assert headers["x-frame-options"] == "DENY"
    assert headers["cache-control"] == "no-store"
    assert headers["referrer-policy"] == "same-origin"


def test_pages_load_nothing_from_other_sites(unlocked):
    for path in ("/", "/settings", "/audit", "/restore"):
        html = unlocked.get(path).text
        assert "http://" not in html and "https://" not in html and "//cdn" not in html
        assert "<style" not in html and "style=" not in html and "onclick" not in html


def test_no_api_docs_exposed(client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_null_origin_posts_are_blocked(unlocked):
    assert unlocked.post("/lock", headers={"origin": "null"}).status_code == 403
    assert core_of(unlocked).unlocked
