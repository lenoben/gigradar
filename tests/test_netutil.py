"""Run: .venv/Scripts/python -m unittest discover -s tests   (offline: a local HTTPS server with its own CA)

Simulates a PC that lacks a root certificate: the server's CA is NOT in the system store, only in the
"certifi" bundle (patched to a file holding that CA). The first attempt must fail, the retry must succeed.
"""

import datetime
import ipaddress
import json
import ssl
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

from gigradar import cli, netutil
from gigradar.telegram import urllib_post

try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
except ImportError:      # cryptography is a dependency of the model download; without it this test cannot run
    x509 = None


def make_pki(directory: Path) -> tuple[Path, Path, Path]:
    """A throw-away CA and a localhost server certificate signed by it. Returns (ca, cert, key) files."""
    now = datetime.datetime.now(datetime.timezone.utc)

    def key() -> rsa.RSAPrivateKey:
        return rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def name(common: str) -> x509.Name:
        return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common)])

    ca_key, server_key = key(), key()
    ca = (x509.CertificateBuilder().subject_name(name("test ca")).issuer_name(name("test ca"))
          .public_key(ca_key.public_key()).serial_number(1).not_valid_before(now - datetime.timedelta(days=1))
          .not_valid_after(now + datetime.timedelta(days=1))
          .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
          .sign(ca_key, hashes.SHA256()))
    server = (x509.CertificateBuilder().subject_name(name("localhost")).issuer_name(ca.subject)
              .public_key(server_key.public_key()).serial_number(2).not_valid_before(now - datetime.timedelta(days=1))
              .not_valid_after(now + datetime.timedelta(days=1))
              .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"),
                                                          x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
                             critical=False)
              .sign(ca_key, hashes.SHA256()))
    files = (directory / "ca.pem", directory / "server.pem", directory / "server.key")
    files[0].write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    files[1].write_bytes(server.public_bytes(serialization.Encoding.PEM))
    files[2].write_bytes(server_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                                  serialization.NoEncryption()))
    return files


class Handler(BaseHTTPRequestHandler):
    posts: list[bytes] = []

    def do_POST(self) -> None:
        Handler.posts.append(self.rfile.read(int(self.headers["Content-Length"])))
        body = json.dumps({"ok": True, "result": {"username": "bot"}}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self) -> None:
        self.send_response(200)
        self.send_header("Content-Length", "4321")
        self.end_headers()

    def log_message(self, *args) -> None:
        pass


@unittest.skipIf(x509 is None, "cryptography is not installed")
class MissingRootCertificateTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ca, cert, key = make_pki(Path(tmp.name))
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f"https://127.0.0.1:{self.server.server_address[1]}/bot123:SECRET/getMe"
        Handler.posts.clear()
        # certifi's bundle is the only place that knows the server's CA
        certifi = mock.patch("certifi.where", return_value=str(ca))
        certifi.start()
        self.addCleanup(certifi.stop)

    def test_the_default_trust_store_rejects_the_server(self) -> None:
        with self.assertRaises(urllib.error.URLError) as caught:
            urllib.request.urlopen(self.url, timeout=5)
        self.assertIsInstance(caught.exception.reason, ssl.SSLCertVerificationError)

    def test_telegram_post_falls_back_to_the_certifi_bundle(self) -> None:
        status, body = urllib_post(self.url, b"chat_id=1&text=hi")
        self.assertEqual((status, json.loads(body)["ok"]), (200, True))
        self.assertEqual(Handler.posts, [b"chat_id=1&text=hi"])      # sent exactly once: the first try never got that far

    def test_the_model_size_lookup_falls_back_too(self) -> None:
        self.assertEqual(cli.head_content_length(self.url), 4321)

    def test_without_certifi_the_original_certificate_error_surfaces(self) -> None:
        with mock.patch.object(netutil, "certifi_context", return_value=None):
            with self.assertRaises(urllib.error.URLError) as caught:
                netutil.urlopen(urllib.request.Request(self.url), 5)
        self.assertIsInstance(caught.exception.reason, ssl.SSLCertVerificationError)

    def test_an_unreachable_host_is_not_retried(self) -> None:
        calls = []

        def refuse(request, timeout, context=None):
            calls.append(context)
            raise urllib.error.URLError(ConnectionRefusedError("refused"))

        with mock.patch("urllib.request.urlopen", refuse):
            with self.assertRaises(urllib.error.URLError):
                netutil.urlopen(urllib.request.Request(self.url), 5)
        self.assertEqual(calls, [None])


if __name__ == "__main__":
    unittest.main()
