"""Tests for ytmusic_auth.py — browser-header auth resolution + CORS."""

from __future__ import annotations

import base64
import json

import pytest
from flask import Flask, Response

import ytmusic_auth


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def app():
    app = Flask(__name__)
    app.secret_key = "test-secret"
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def _b64(payload: dict) -> str:
    return base64.b64encode(json.dumps(payload).encode()).decode()


# ---------------------------------------------------------------------------
# _decode_auth_header (pure function, no Flask)
# ---------------------------------------------------------------------------

class TestDecodeAuthHeader:
    def test_valid_payload(self):
        raw = _b64({"Authorization": "Bearer x", "X-Goog": "1"})
        assert ytmusic_auth._decode_auth_header(raw) == {
            "Authorization": "Bearer x",
            "X-Goog": "1",
        }

    def test_empty_dict_rejected(self):
        with pytest.raises(ValueError, match="JSON object"):
            ytmusic_auth._decode_auth_header(_b64({}))

    def test_non_dict_payload_rejected(self):
        for payload in ([1, 2], "string", 42, None, True):
            with pytest.raises(ValueError, match="JSON object"):
                ytmusic_auth._decode_auth_header(_b64(payload))  # type: ignore[arg-type]

    def test_invalid_base64_raises(self):
        with pytest.raises(ValueError, match="Invalid"):
            ytmusic_auth._decode_auth_header("!!!not base64!!!")

    def test_valid_base64_invalid_json_raises(self):
        bad = base64.b64encode(b"not json at all").decode()
        with pytest.raises(ValueError, match="Invalid"):
            ytmusic_auth._decode_auth_header(bad)

    def test_non_utf8_decoded_bytes_raises(self):
        bad = base64.b64encode(b"\xff\xfe\xfd").decode()
        with pytest.raises(ValueError, match="Invalid"):
            ytmusic_auth._decode_auth_header(bad)


# ---------------------------------------------------------------------------
# auth_dict_from_request — header path
# ---------------------------------------------------------------------------

class TestAuthFromHeader:
    def test_valid_header(self, app):
        payload = {"Authorization": "Bearer t"}
        with app.test_request_context(
            headers={ytmusic_auth.AUTH_HEADER: _b64(payload)}
        ):
            assert ytmusic_auth.auth_dict_from_request() == payload

    def test_malformed_header_raises(self, app):
        with app.test_request_context(
            headers={ytmusic_auth.AUTH_HEADER: "not-base64"}
        ):
            with pytest.raises(ValueError, match="Invalid"):
                ytmusic_auth.auth_dict_from_request()

    def test_blank_header_falls_through_to_session(self, app):
        with app.test_request_context(
            headers={ytmusic_auth.AUTH_HEADER: "   "}
        ):
            assert ytmusic_auth.auth_dict_from_request() is None

    def test_empty_after_strip_falls_through(self, app):
        with app.test_request_context(headers={ytmusic_auth.AUTH_HEADER: ""}):
            assert ytmusic_auth.auth_dict_from_request() is None


# ---------------------------------------------------------------------------
# auth_dict_from_request — session path
# ---------------------------------------------------------------------------

class TestAuthFromSession:
    def test_no_header_no_session_returns_none(self, app):
        with app.test_request_context():
            assert ytmusic_auth.auth_dict_from_request() is None

    def test_session_dict_returned(self, app):
        stored = {"Cookie": "abc"}
        with app.test_request_context():
            from flask import session
            session[ytmusic_auth.SESSION_KEY] = stored
            assert ytmusic_auth.auth_dict_from_request() == stored

    def test_session_non_dict_ignored(self, app):
        with app.test_request_context():
            from flask import session
            session[ytmusic_auth.SESSION_KEY] = "not a dict"
            assert ytmusic_auth.auth_dict_from_request() is None

    def test_session_empty_dict_ignored(self, app):
        with app.test_request_context():
            from flask import session
            session[ytmusic_auth.SESSION_KEY] = {}
            assert ytmusic_auth.auth_dict_from_request() is None

    def test_header_takes_precedence_over_session(self, app):
        header_payload = {"Authorization": "Bearer header"}
        stored = {"Authorization": "Bearer session"}
        with app.test_request_context(
            headers={ytmusic_auth.AUTH_HEADER: _b64(header_payload)}
        ):
            from flask import session
            session[ytmusic_auth.SESSION_KEY] = stored
            assert ytmusic_auth.auth_dict_from_request() == header_payload


# ---------------------------------------------------------------------------
# store_auth_in_session / clear_auth_session
# ---------------------------------------------------------------------------

class TestSessionHelpers:
    def test_store_sets_value_and_modified(self, app):
        with app.test_request_context():
            from flask import session
            ytmusic_auth.store_auth_in_session({"a": "b"})
            assert session[ytmusic_auth.SESSION_KEY] == {"a": "b"}
            assert session.modified is True

    def test_clear_removes_value(self, app):
        with app.test_request_context():
            from flask import session
            session[ytmusic_auth.SESSION_KEY] = {"x": "y"}
            ytmusic_auth.clear_auth_session()
            assert ytmusic_auth.SESSION_KEY not in session

    def test_clear_missing_key_is_noop(self, app):
        with app.test_request_context():
            # Should not raise even if nothing stored
            ytmusic_auth.clear_auth_session()


# ---------------------------------------------------------------------------
# ytmusic_from_request
# ---------------------------------------------------------------------------

class TestYtmusicFromRequest:
    def test_no_auth_returns_lazily_initialized_default(self, app, monkeypatch):
        # Reset the module cache so we observe the lazy init
        monkeypatch.setattr(ytmusic_auth, "_default_yt", None)

        constructed = []

        class FakeYT:
            def __init__(self, auth=None):
                constructed.append(auth)

        monkeypatch.setattr(ytmusic_auth, "YTMusic", FakeYT)

        with app.test_request_context():
            first = ytmusic_auth.ytmusic_from_request()
            second = ytmusic_auth.ytmusic_from_request()

        # Built exactly once, with no args (auth=None)
        assert constructed == [None]
        # Both calls return the same cached instance
        assert first is second
        assert first is ytmusic_auth._default_yt

    def test_default_client_is_cached(self, app, monkeypatch):
        monkeypatch.setattr(ytmusic_auth, "_default_yt", None)
        monkeypatch.setattr(ytmusic_auth, "YTMusic", lambda auth=None: object())
        with app.test_request_context():
            a = ytmusic_auth.ytmusic_from_request()
            b = ytmusic_auth.ytmusic_from_request()
        assert a is b

    def test_auth_request_does_not_initialize_default(self, app, monkeypatch):
        monkeypatch.setattr(ytmusic_auth, "_default_yt", None)
        monkeypatch.setattr(ytmusic_auth, "YTMusic", lambda auth=None: object())
        with app.test_request_context(
            headers={ytmusic_auth.AUTH_HEADER: _b64({"Authorization": "Bearer t"})}
        ):
            ytmusic_auth.ytmusic_from_request()
        assert ytmusic_auth._default_yt is None

    def test_auth_present_constructs_new_client(self, app, monkeypatch):
        constructed = []

        class FakeYT:
            def __init__(self, auth=None):
                constructed.append(auth)
                self.auth = auth

        monkeypatch.setattr(ytmusic_auth, "YTMusic", FakeYT)
        payload = {"Authorization": "Bearer t"}
        with app.test_request_context(
            headers={ytmusic_auth.AUTH_HEADER: _b64(payload)}
        ):
            client = ytmusic_auth.ytmusic_from_request()
        assert constructed == [payload]
        assert client.auth == payload

    def test_session_auth_constructs_new_client(self, app, monkeypatch):
        constructed = []

        class FakeYT:
            def __init__(self, auth=None):
                constructed.append(auth)

        monkeypatch.setattr(ytmusic_auth, "YTMusic", FakeYT)
        with app.test_request_context():
            from flask import session
            session[ytmusic_auth.SESSION_KEY] = {"Cookie": "c"}
            ytmusic_auth.ytmusic_from_request()
        assert constructed == [{"Cookie": "c"}]


# ---------------------------------------------------------------------------
# _origin_allowed
# ---------------------------------------------------------------------------

class TestOriginAllowed:
    @pytest.mark.parametrize("origin", [
        "http://localhost:3000",
        "http://localhost",
        "http://127.0.0.1:5000",
        "https://127.0.0.1",
        "https://myapp.vercel.app",
        "https://foo-bar.netlify.app",
    ])
    def test_default_allowed(self, origin, monkeypatch):
        monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)
        assert ytmusic_auth._origin_allowed(origin) is True

    @pytest.mark.parametrize("origin", [
        "https://evil.com",
        "https://localhost.evil.com",   # host is "localhost.evil.com"
        "ftp://localhost",              # scheme rejected
        "file://localhost",             # scheme rejected
        "https://example.com",
        "not-a-url",
        "",
    ])
    def test_default_rejected(self, origin, monkeypatch):
        monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)
        assert ytmusic_auth._origin_allowed(origin) is False

    def test_env_extra_origin_allowed(self, monkeypatch):
        monkeypatch.setenv(
            "CORS_ALLOWED_ORIGINS", "https://app.example.com,https://other.com"
        )
        assert ytmusic_auth._origin_allowed("https://app.example.com") is True
        assert ytmusic_auth._origin_allowed("https://other.com") is True

    def test_env_extra_does_not_block_defaults(self, monkeypatch):
        monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://app.example.com")
        assert ytmusic_auth._origin_allowed("http://localhost:3000") is True

    def test_env_whitespace_trimmed(self, monkeypatch):
        monkeypatch.setenv(
            "CORS_ALLOWED_ORIGINS", "  https://a.com  ,  https://b.com  "
        )
        assert ytmusic_auth._origin_allowed("https://a.com") is True
        assert ytmusic_auth._origin_allowed("https://b.com") is True

    def test_env_empty_entries_ignored(self, monkeypatch):
        monkeypatch.setenv("CORS_ALLOWED_ORIGINS", ",,,")
        assert ytmusic_auth._origin_allowed("http://localhost") is True
        assert ytmusic_auth._origin_allowed("https://evil.com") is False

    def test_env_origin_must_match_exactly(self, monkeypatch):
        # "https://app.example.com/" (trailing slash) should NOT match
        monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://app.example.com")
        assert ytmusic_auth._origin_allowed("https://app.example.com/") is False


# ---------------------------------------------------------------------------
# apply_cors
# ---------------------------------------------------------------------------

class TestApplyCors:
    def _resp(self):
        return Response("ok")

    def test_allowed_origin_sets_headers(self, app):
        with app.test_request_context(
            headers={"Origin": "http://localhost:3000"},
            method="GET",
        ):
            resp = ytmusic_auth.apply_cors(self._resp())
        assert resp.headers["Access-Control-Allow-Origin"] == "http://localhost:3000"
        assert resp.headers["Access-Control-Allow-Credentials"] == "true"
        assert "X-YTMusic-Auth" in resp.headers["Access-Control-Allow-Headers"]
        assert "POST" in resp.headers["Access-Control-Allow-Methods"]
        assert resp.headers["Vary"] == "Origin"
        assert "Access-Control-Max-Age" not in resp.headers

    def test_disallowed_origin_no_headers(self, app):
        with app.test_request_context(
            headers={"Origin": "https://evil.com"},
            method="GET",
        ):
            resp = ytmusic_auth.apply_cors(self._resp())
        assert "Access-Control-Allow-Origin" not in resp.headers

    def test_missing_origin_no_headers(self, app):
        with app.test_request_context(method="GET"):
            resp = ytmusic_auth.apply_cors(self._resp())
        assert "Access-Control-Allow-Origin" not in resp.headers

    def test_preflight_sets_max_age(self, app):
        with app.test_request_context(
            headers={"Origin": "http://localhost:3000"},
            method="OPTIONS",
        ):
            resp = ytmusic_auth.apply_cors(self._resp())
        assert resp.headers["Access-Control-Max-Age"] == "86400"

    def test_get_request_no_max_age(self, app):
        with app.test_request_context(
            headers={"Origin": "http://localhost:3000"},
            method="GET",
        ):
            resp = ytmusic_auth.apply_cors(self._resp())
        assert "Access-Control-Max-Age" not in resp.headers

    def test_env_origin_applies_headers(self, app, monkeypatch):
        monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://app.example.com")
        with app.test_request_context(
            headers={"Origin": "https://app.example.com"},
            method="GET",
        ):
            resp = ytmusic_auth.apply_cors(self._resp())
        assert resp.headers["Access-Control-Allow-Origin"] == "https://app.example.com"