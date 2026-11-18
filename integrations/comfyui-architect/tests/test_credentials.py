import json
import os
from pathlib import Path
import tempfile
import unittest
from workflow_architect.credentials import CredentialVault,scope_digest
from workflow_architect.api_budget import REQUESTED_PROFILE
from workflow_architect.errors import InputError

@unittest.skipUnless(os.name=='nt','Windows DPAPI acceptance')
class CredentialTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.v=CredentialVault(Path(self.tmp.name)/'workflow_architect'/'credentials')
    def test_status_does_not_create(self):
        self.assertFalse(self.v.status()['local_credential_present']);self.assertFalse(self.v.root.exists())
    def test_roundtrip_ciphertext_only(self):
        key='owned-fake-key-not-provider-123'
        result=self.v.store(key);self.assertFalse(result['network_enabled'])
        self.assertEqual(self.v.read({'profile':REQUESTED_PROFILE}),key)
        for f in self.v.root.iterdir():self.assertNotIn(key.encode(),f.read_bytes())
        self.assertNotIn(key,json.dumps(self.v.status()))
    def test_no_silent_overwrite_and_rotation(self):
        self.v.store('owned-fake-first-key')
        with self.assertRaises(InputError):self.v.store('owned-fake-second-key')
        self.assertTrue(self.v.store('owned-fake-second-key',rotate=True)['rotated'])
        self.assertEqual(len(list(self.v.root.glob('*.previous-*.dpapi'))),1)
    def test_scope_and_corruption(self):
        with self.assertRaises(InputError):scope_digest({**REQUESTED_PROFILE,'model':'other'})
        self.v.store('owned-fake-key');self.v.path.write_bytes(b'WAC1corrupt')
        with self.assertRaises(InputError):self.v.read({'profile':REQUESTED_PROFILE})
    def test_invalid_key_and_root(self):
        for key in ('short','owned fake key','owned-key\n'):
            with self.assertRaises(InputError):self.v.store(key)
        with self.assertRaises(InputError):CredentialVault(Path(self.tmp.name)/'wrong')
