import unittest
from uuid import uuid4

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from visual_platform.core.identity import (
    decode_access,
    hash_password,
    issue_access,
    new_refresh,
    verify_password,
)


class IdentityTests(unittest.TestCase):
    def test_password_and_refresh(self):
        h = hash_password("test password 1234")
        self.assertTrue(verify_password(h, "test password 1234"))
        self.assertFalse(verify_password(h, "wrong"))
        a, ah = new_refresh()
        b, bh = new_refresh()
        self.assertNotEqual(a, b)
        self.assertNotEqual(a, ah)
        self.assertEqual(len(ah), 64)

    def test_fixed_rs256_and_audience(self):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        private = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        public = key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        u = uuid4()
        token = issue_access(u, private, issuer="test", audience="visual")
        self.assertEqual(
            decode_access(token, public, issuer="test", audience="visual")["sub"], str(u)
        )
        with self.assertRaises(jwt.InvalidAudienceError):
            decode_access(token, public, issuer="test", audience="other")
        with self.assertRaises(jwt.InvalidAlgorithmError):
            decode_access(
                jwt.encode({"sub": str(u)}, "x" * 64, algorithm="HS256"),
                public,
                issuer="test",
                audience="visual",
            )
