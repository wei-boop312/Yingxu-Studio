import os
import unittest
from dataclasses import replace
from io import BytesIO
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import httpx
import psycopg
from PIL import Image
from test_auth_api import AuthApiFixture


@unittest.skipUnless(os.environ.get("TEST_S3_ENDPOINT"), "native S3 not configured")
class AssetFixture(AuthApiFixture):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.settings = replace(
            cls.settings,
            s3_endpoint=os.environ["TEST_S3_ENDPOINT"],
            s3_access_key=os.environ["TEST_S3_ACCESS_KEY"],
            s3_secret_key=os.environ["TEST_S3_SECRET_KEY"],
            s3_bucket=os.environ["TEST_S3_BUCKET"],
        )

    def setUp(self):
        super().setUp()
        self.login()
        self.product = self.client.post(
            "/api/v1/products",
            json={"name": "asset", "category": "generic"},
            headers=self.write_headers(),
        ).json()["id"]

    def upload(self, data, mime="image/png"):
        r = self.client.post(
            "/api/v1/assets/upload-intents",
            json={
                "product_id": self.product,
                "filename": "product.png",
                "mime": mime,
                "size": len(data),
            },
            headers=self.write_headers(),
        )
        self.assertEqual(r.status_code, 201, r.text)
        info = r.json()
        result = httpx.put(info["upload_url"], content=data, headers=info["headers"], timeout=15)
        self.assertEqual(result.status_code, 200, result.text)
        return info

    def complete(self, info):
        return self.client.post(
            "/api/v1/assets/upload-intents/" + info["intent_id"] + "/complete",
            headers=self.write_headers(),
        )

    def png(self):
        im = Image.new("RGBA", (10, 20), (255, 0, 0, 0))
        exif = Image.Exif()
        exif[274] = 6
        exif[270] = "private location"
        b = BytesIO()
        im.save(b, "PNG", exif=exif)
        return b.getvalue()


class AssetTests(AssetFixture):
    def test_upload_normalize_private_download_and_replay(self):
        data = self.png()
        info = self.upload(data)
        result = self.complete(info)
        self.assertEqual(result.status_code, 200, result.text)
        version = result.json()
        self.assertEqual((version["width"], version["height"]), (20, 10))
        self.assertTrue(version["canonical"])
        self.assertEqual(self.complete(info).json()["version_id"], version["version_id"])
        redirect = self.client.get(
            "/api/v1/assets/" + info["asset_id"] + "/download", follow_redirects=False
        )
        self.assertEqual(redirect.status_code, 302, redirect.text)
        response = httpx.get(redirect.headers["location"], timeout=10)
        self.assertEqual(response.status_code, 200, response.text)
        with Image.open(BytesIO(response.content)) as image:
            self.assertEqual(image.mode, "RGB")
            self.assertFalse(image.getexif())
            self.assertEqual(image.getpixel((0, 0)), (255, 255, 255))
        u = urlsplit(redirect.headers["location"])
        public = urlunsplit((u.scheme, u.netloc, u.path, "", ""))
        self.assertEqual(httpx.get(public, timeout=10).status_code, 403)
        with psycopg.connect(self.owner) as c:
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM app.asset_versions WHERE asset_id=%s", (info["asset_id"],)
                ).fetchone()[0],
                2,
            )
            original = c.execute(
                "SELECT original_version_id FROM app.upload_intents WHERE id=%s",
                (info["intent_id"],),
            ).fetchone()[0]
        raw_url = self.client.get(
            "/api/v1/assets/" + info["asset_id"] + "/download",
            params={"version_id": str(original)},
            follow_redirects=False,
        ).headers["location"]
        self.assertEqual(httpx.get(raw_url, timeout=10).content, data)

    def test_invalid_decode_and_mime_are_rejected(self):
        for data in (b"not an image", self.jpeg()):
            info = self.upload(data)
            r = self.complete(info)
            self.assertEqual(r.status_code, 422, r.text)
            self.assertEqual(
                self.client.get("/api/v1/assets/" + info["asset_id"]).json()["status"], "rejected"
            )

    def jpeg(self):
        b = BytesIO()
        Image.new("RGB", (10, 10), "red").save(b, "JPEG")
        return b.getvalue()

    def test_animation_rejected(self):
        b = BytesIO()
        Image.new("RGB", (10, 10), "red").save(
            b,
            "PNG",
            save_all=True,
            append_images=[Image.new("RGB", (10, 10), "blue")],
            duration=100,
            loop=0,
        )
        r = self.complete(self.upload(b.getvalue()))
        self.assertEqual(r.status_code, 422, r.text)

    def test_cross_user_upload_intent_hidden(self):
        self.assertEqual(
            self.client.post(
                "/api/v1/assets/upload-intents/" + str(uuid4()) + "/complete",
                headers=self.write_headers(),
            ).status_code,
            404,
        )
        self.assertEqual(self.client.get("/api/v1/assets/" + str(uuid4())).status_code, 404)

    def test_known_other_user_asset_and_intent_are_hidden(self):
        from visual_platform.core.identity import hash_password

        info = self.upload(self.png())
        self.assertEqual(self.complete(info).status_code, 200)
        other = uuid4()
        email = str(other) + "@test.invalid"
        with psycopg.connect(self.owner) as c:
            c.execute("SET SESSION AUTHORIZATION ops_admin")
            c.execute(
                "SELECT app.create_private_user(%s,%s,'other',%s)",
                (other, email, hash_password(self.password)),
            )
        self.email = email
        self.login()
        self.assertEqual(self.client.get("/api/v1/assets/" + info["asset_id"]).status_code, 404)
        self.assertEqual(
            self.client.get(
                "/api/v1/assets/" + info["asset_id"] + "/download", follow_redirects=False
            ).status_code,
            404,
        )
        self.assertEqual(self.complete(info).status_code, 404)

    def test_declared_upload_limits(self):
        for size in (0, True, 20 * 1024 * 1024 + 1):
            r = self.client.post(
                "/api/v1/assets/upload-intents",
                json={
                    "product_id": self.product,
                    "filename": "test",
                    "mime": "image/png",
                    "size": size,
                },
                headers=self.write_headers(),
            )
            self.assertEqual(r.status_code, 422)

    def test_storage_conditional_put_preserves_original(self):
        from visual_platform.storage.s3 import StorageError

        key = "immutable-test/" + str(uuid4())
        storage = self.client.app.state.storage
        storage.put_immutable(key, b"abc", "application/octet-stream")
        storage.put_immutable(key, b"abc", "application/octet-stream")
        with self.assertRaises(StorageError):
            storage.put_immutable(key, b"xyz", "application/octet-stream")
        self.assertEqual(storage.get(key)[0], b"abc")

    def test_concurrent_complete_only_two_versions(self):
        from concurrent.futures import ThreadPoolExecutor

        info = self.upload(self.png())
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.complete(info), range(2)))
        self.assertTrue(
            all(r.status_code in (200, 409) for r in results), [r.text for r in results]
        )
        self.assertTrue(any(r.status_code == 200 for r in results))
        self.assertEqual(self.complete(info).status_code, 200)
        with psycopg.connect(self.owner) as c:
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM app.asset_versions WHERE asset_id=%s", (info["asset_id"],)
                ).fetchone()[0],
                2,
            )
