"""Real PG/auth cookies with ASGI HTTPS simulation, not a deployed TLS endpoint."""

from dataclasses import replace

import psycopg
from fastapi.testclient import TestClient
from test_auth_api import AuthApiFixture

from visual_platform.api.main import create_app


class OriginStartupTests(AuthApiFixture):
    def secure_client(self):
        settings = replace(self.settings, origin="HTTPS://APP.EXAMPLE:443", cookie_secure=True)
        self.assertEqual(settings.origin, "https://app.example")
        return TestClient(create_app(settings), base_url="https://app.example")

    def login_secure(self, client):
        result = client.post(
            "/api/v1/auth/login",
            json={"email": self.email, "password": self.password},
            headers={"origin": "https://app.example"},
        )
        self.assertEqual(result.status_code, 200)
        return result

    def test_normalized_origin_supports_real_session_and_secure_cookies_stay_off_http(self):
        with self.secure_client() as client:
            result = self.login_secure(client)
            headers = result.headers.get_list("set-cookie")
            self.assertEqual(len(headers), 3)
            self.assertTrue(
                all("Secure" in value and "SameSite=strict" in value for value in headers)
            )
            self.assertEqual(client.get("/api/v1/auth/me").status_code, 200)
            self.assertEqual(client.get("http://app.example/api/v1/auth/me").status_code, 401)
            csrf = client.cookies["visual_csrf"]
            created = client.post(
                "/api/v1/products",
                json={"name": "Normalized origin product", "category": "generic"},
                headers={"origin": "https://app.example", "x-csrf-token": csrf},
            )
            self.assertEqual(created.status_code, 201)

    def test_request_origin_stays_exact_after_operator_origin_normalization(self):
        with self.secure_client() as client:
            self.login_secure(client)
            for origin in (
                "https://app.example:443",
                "https://evil.example",
                "https://app.example?token=fixture",
                "https://user:fixture@app.example",
                "http://app.example",
            ):
                result = client.post(
                    "/api/v1/products",
                    json={"name": "must-not-create", "category": "generic"},
                    headers={"origin": origin, "x-csrf-token": client.cookies["visual_csrf"]},
                )
                with self.subTest(origin=origin):
                    self.assertEqual(result.status_code, 403)
        with psycopg.connect(self.owner) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM app.products WHERE user_id=%s", (self.user,)
                ).fetchone()[0],
                0,
            )
