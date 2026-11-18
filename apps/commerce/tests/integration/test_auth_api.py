import os
import secrets
import unittest
from urllib.parse import quote, urlsplit, urlunsplit
from uuid import uuid4

import psycopg
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from psycopg import sql

from visual_platform.api.main import create_app
from visual_platform.core.identity import hash_password
from visual_platform.core.settings import Settings


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "native PostgreSQL not configured")
class AuthApiFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.owner = os.environ["TEST_DATABASE_URL"]
        cls.roles = []
        urls = []
        with psycopg.connect(cls.owner) as c:
            for group in ("app_api", "app_auth"):
                name = "test_" + uuid4().hex
                password = secrets.token_urlsafe(32)
                cls.roles.append(name)
                c.execute(
                    sql.SQL(
                        "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE"
                    ).format(sql.Identifier(name), sql.Literal(password))
                )
                c.execute(
                    sql.SQL("GRANT {} TO {}").format(sql.Identifier(group), sql.Identifier(name))
                )
                u = urlsplit(cls.owner)
                urls.append(
                    urlunsplit(
                        (
                            u.scheme,
                            quote(name)
                            + ":"
                            + quote(password)
                            + "@"
                            + u.hostname
                            + ":"
                            + str(u.port or 5432),
                            u.path,
                            u.query,
                            u.fragment,
                        )
                    )
                )
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        private = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()
        public = (
            key.public_key()
            .public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
            )
            .decode()
        )
        cls.settings = Settings(urls[0], urls[1], private, public, "http://localhost", False)

    @classmethod
    def tearDownClass(cls):
        with psycopg.connect(cls.owner) as c:
            for role in cls.roles:
                c.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))

    def setUp(self):
        self.user = uuid4()
        self.email = str(self.user) + "@test.invalid"
        self.password = "password for tests 1234"
        with psycopg.connect(self.owner) as c:
            c.execute("DELETE FROM auth.login_limits")
            c.execute(
                "INSERT INTO app.users(id,email_normalized,display_name,status) VALUES(%s,%s,'tester','active')",
                (self.user, self.email),
            )
            c.execute(
                "INSERT INTO auth.credentials(user_id,password_hash) VALUES(%s,%s)",
                (self.user, hash_password(self.password)),
            )
        self.client = TestClient(create_app(self.settings))
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def login(self):
        result = self.client.post(
            "/api/v1/auth/login",
            json=dict(email=self.email, password=self.password),
            headers={"origin": self.settings.origin},
        )
        self.assertEqual(result.status_code, 200, result.text)
        return result

    def write_headers(self):
        return {"origin": self.settings.origin, "x-csrf-token": self.client.cookies["visual_csrf"]}


class AuthApiTests(AuthApiFixture):
    def test_login_logout_revokes_access(self):
        result = self.login()
        cookie = "; ".join(k + "=" + v for k, v in self.client.cookies.items())
        self.assertIn("HttpOnly", result.headers.get_list("set-cookie")[0])
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, 200)
        self.assertEqual(
            self.client.post("/api/v1/auth/logout", headers=self.write_headers()).status_code, 204
        )
        self.assertEqual(
            self.client.get("/api/v1/auth/me", headers={"cookie": cookie}).status_code, 401
        )

    def test_rotation_replay_revokes_family(self):
        self.login()
        old_cookie = "; ".join(k + "=" + v for k, v in self.client.cookies.items())
        headers = self.write_headers()
        self.assertEqual(self.client.post("/api/v1/auth/refresh", headers=headers).status_code, 200)
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, 200)
        self.assertEqual(
            self.client.post(
                "/api/v1/auth/refresh", headers={**headers, "cookie": old_cookie}
            ).status_code,
            401,
        )
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, 401)

    def test_origin_csrf_and_extra_identity(self):
        origin = self.client.post(
            "/api/v1/auth/login", json=dict(email=self.email, password=self.password)
        )
        self.assertEqual(origin.status_code, 403)
        self.login()
        csrf = self.client.post("/api/v1/products", json=dict(name="x", category="generic"))
        self.assertEqual(csrf.status_code, 403)
        r = self.client.post(
            "/api/v1/products",
            json=dict(name="x", category="generic", user_id=str(uuid4())),
            headers=self.write_headers(),
        )
        self.assertEqual(r.status_code, 422)
        self.assertNotIn("password", r.text)
        for response in (origin, csrf, r):
            self.assertEqual(
                response.headers["x-request-id"], response.json()["error"]["request_id"]
            )
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertEqual(response.headers["x-content-type-options"], "nosniff")

    def test_products_pagination_revision_archive_and_isolation(self):
        self.login()
        ids = []
        for n in range(3):
            result = self.client.post(
                "/api/v1/products",
                json=dict(name="p" + str(n), category="generic"),
                headers=self.write_headers(),
            )
            self.assertEqual(result.status_code, 201, result.text)
            ids.append(result.json()["id"])
        page = self.client.get("/api/v1/products?limit=2").json()
        self.assertEqual(len(page["items"]), 2)
        page2 = self.client.get(
            "/api/v1/products", params={"limit": 2, "cursor": page["next_cursor"]}
        ).json()
        self.assertEqual(len(page2["items"]), 1)
        self.assertFalse(set(x["id"] for x in page["items"]) & set(x["id"] for x in page2["items"]))
        self.assertEqual(
            self.client.patch(
                "/api/v1/products/" + ids[0],
                json={"name": "new"},
                headers={**self.write_headers(), "if-match": "1"},
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.patch(
                "/api/v1/products/" + ids[0],
                json={"name": "bad"},
                headers={**self.write_headers(), "if-match": "1"},
            ).status_code,
            409,
        )
        stranger = uuid4()
        product = uuid4()
        with psycopg.connect(self.owner) as c:
            c.execute(
                "INSERT INTO app.users(id,email_normalized,display_name,status) VALUES(%s,%s,'stranger','active')",
                (stranger, str(stranger) + "@test.invalid"),
            )
            c.execute(
                "INSERT INTO app.products(id,user_id,name,category) VALUES(%s,%s,'private','generic')",
                (product, stranger),
            )
        self.assertEqual(self.client.get("/api/v1/products/" + str(product)).status_code, 404)
        self.assertEqual(
            self.client.delete(
                "/api/v1/products/" + ids[0], headers=self.write_headers()
            ).status_code,
            204,
        )
        self.assertIsNotNone(self.client.get("/api/v1/products/" + ids[0]).json()["archived_at"])

    def test_disabled_account_and_rate_limit(self):
        for _ in range(10):
            r = self.client.post(
                "/api/v1/auth/login",
                json=dict(email=self.email, password="wrong"),
                headers={"origin": self.settings.origin},
            )
            self.assertEqual(r.status_code, 401)
        self.assertEqual(
            self.client.post(
                "/api/v1/auth/login",
                json=dict(email=self.email, password=self.password),
                headers={"origin": self.settings.origin},
            ).status_code,
            429,
        )

    def test_disabled_access_immediate(self):
        self.login()
        with psycopg.connect(self.owner) as c:
            c.execute("UPDATE app.users SET status='disabled' WHERE id=%s", (self.user,))
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, 401)

    def test_private_account_creation_only_admin(self):
        uid = uuid4()
        email = str(uid) + "@test.invalid"
        with psycopg.connect(self.owner) as c:
            c.execute("SET SESSION AUTHORIZATION ops_admin")
            c.execute(
                "SELECT app.create_private_user(%s,%s,'new account',%s)",
                (uid, email, hash_password(self.password)),
            )
            c.execute("SELECT app.grant_credits(%s,%s,100,'initial')", (uid, uuid4()))
        with psycopg.connect(self.owner) as c:
            c.execute("SET SESSION AUTHORIZATION app_api")
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                c.execute(
                    "SELECT app.create_private_user(%s,%s,'forbidden',%s)",
                    (uuid4(), "forbidden@test.invalid", hash_password(self.password)),
                )
        self.email = email
        self.login()
        self.assertEqual(self.client.get("/api/v1/credits/balance").json()["available"], 100)

    def test_ip_limit_is_shared_across_account_names(self):
        from visual_platform.api.auth import digest

        with psycopg.connect(self.owner) as c:
            c.execute(
                "INSERT INTO auth.login_limits(bucket,attempts) VALUES(%s,100)",
                ("ip:" + digest("testclient"),),
            )
        response = self.client.post(
            "/api/v1/auth/login",
            json=dict(email=self.email, password=self.password),
            headers={"origin": self.settings.origin},
        )
        self.assertEqual(response.status_code, 429)
