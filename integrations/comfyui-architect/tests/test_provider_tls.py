"""Owned loopback TLS with test-only injected public DNS/socket/CA. No supplier."""
import datetime
import http.server
from pathlib import Path
import socket
import ssl
import tempfile
import threading
import unittest
from unittest.mock import patch
from workflow_architect.provider_support import https_once
from workflow_architect.errors import InputError

class ProviderTlsTests(unittest.TestCase):
    def setUp(self):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes,serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
        self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name)
        key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'owned.invalid')])
        now=datetime.datetime.now(datetime.timezone.utc)
        cert=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
              .serial_number(x509.random_serial_number()).not_valid_before(now-datetime.timedelta(minutes=1))
              .not_valid_after(now+datetime.timedelta(days=1)).add_extension(x509.SubjectAlternativeName([x509.DNSName('owned.invalid')]),critical=False)
              .add_extension(x509.BasicConstraints(ca=True,path_length=None),critical=True).sign(key,hashes.SHA256()))
        certpath=root/'ca.pem';keypath=root/'key.pem'
        certpath.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        keypath.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        self.status=200;self.content_type='application/json';self.count=0
        owner=self
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self,*_):pass
            def do_POST(self):
                owner.count+=1;owner.host=self.headers.get('Host');owner.body=self.rfile.read(int(self.headers['Content-Length']))
                self.send_response(owner.status);self.send_header('Content-Type',owner.content_type)
                if owner.status==302:self.send_header('Location','https://elsewhere.invalid/')
                self.send_header('Content-Length','2');self.send_header('Connection','close');self.end_headers();self.wfile.write(b'{}')
        self.server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(certpath,keypath)
        self.server.socket=context.wrap_socket(self.server.socket,server_side=True)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.client_context=ssl.create_default_context(cafile=str(certpath))
        self.real_connect=socket.create_connection
        self.profile={'endpoint':'https://owned.invalid/v1/chat/completions','model':'owned','protocol':'chat_completions','token_parameter':'max_completion_tokens','max_output_tokens':512,'timeout_seconds':5}
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join(2);self.tmp.cleanup()
    def connect_owned(self,address,timeout=None,*args,**kwargs):
        self.assertEqual(address,('8.8.8.8',443))
        return self.real_connect(('127.0.0.1',self.server.server_port),timeout)
    def run_owned(self,trusted=True):
        with patch('workflow_architect.provider_support.public_addresses',return_value=['8.8.8.8']),patch('socket.create_connection',side_effect=self.connect_owned):
            if trusted:
                with patch('ssl.create_default_context',return_value=self.client_context):return https_once(self.profile,'owned-fixture-key',b'{}',threading.Event())
            return https_once(self.profile,'owned-fixture-key',b'{}',threading.Event())
    def test_actual_tls_and_http_body(self):
        self.assertEqual(self.run_owned(),b'{}');self.assertEqual(self.count,1);self.assertEqual(self.host,'owned.invalid');self.assertEqual(self.body,b'{}')
    def test_redirect_and_nonjson_no_retry(self):
        self.status=302
        with self.assertRaises(InputError):self.run_owned()
        self.assertEqual(self.count,1)
        self.status=200;self.content_type='text/html'
        with self.assertRaises(InputError):self.run_owned()
        self.assertEqual(self.count,2)
    def test_real_untrusted_ca_no_post(self):
        with self.assertRaises(ssl.SSLCertVerificationError):self.run_owned(trusted=False)
        self.assertEqual(self.count,0)
