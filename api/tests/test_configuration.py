"""Guards on configuration, secrets, and the no-network rule itself.

These are regression tests for defects that were actually present:
``DEBUG = True``, ``ALLOWED_HOSTS = ['*']`` and a Django signing key committed
to the repository, alongside live-looking QuickBooks and Xero OAuth client
secrets in ``example.env`` — plus a provider SDK that opened a socket from a
constructor.

The leaked values are matched by SHA-256 rather than stored literally, so
scrubbing them from the tree did not mean re-introducing them here.
"""

from __future__ import annotations

import hashlib
import re
import socket
import subprocess
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from api.ledger import UnknownProviderError
from api.services import default_provider, provider_config, shared_transport

from .support import NoNetworkMixin

REPO_ROOT = Path(__file__).resolve().parents[2]

#: SHA-256 of each credential that was committed: the Django SECRET_KEY, then
#: the Xero and QuickBooks client ids and secrets from example.env.
LEAKED_DIGESTS = frozenset({
    "921a86aaf5f9551c214f48136981ecc93f40ebc61591498bb7adc394edf88993",
    "ba39d7750ae8652703f5abea8ffc03ebbd3221f11f7a68b313e9d08f15a31ecc",
    "f85662f2e75b9cdf7f2647a06bb4a8bab71191b97ea3fa5c3f9d87e0878c1752",
    "b4a97ddafcd0cf7bf71e165a87c07f3f73e1eddbab9ce3f48172096dd7ca6357",
    "2064b98135af27bf2870180d852ff58914b470be5c6ffccd91e72fd0c1fa75e5",
})

#: A run of non-delimiter characters long enough to be a credential. Quotes,
#: whitespace, `=` and brackets are treated as boundaries, which is how each
#: leaked value appeared in the source it was committed in.
_CANDIDATE = re.compile(r"[^\s'\"=,()\[\]{}]{16,}")

BINARY_SUFFIXES = {".png", ".jpg", ".woff", ".woff2", ".ttf", ".eot", ".svg", ".sqlite3"}


def digests_in(text: str) -> set[str]:
    """SHA-256 of every credential-shaped token in ``text``."""
    return {
        hashlib.sha256(token.encode()).hexdigest()
        for token in _CANDIDATE.findall(text)
    }


class SettingsSafetyTests(SimpleTestCase):
    def test_allowed_hosts_is_never_a_wildcard(self):
        self.assertNotIn("*", settings.ALLOWED_HOSTS)

    def test_secret_key_is_not_one_of_the_leaked_credentials(self):
        digest = hashlib.sha256(settings.SECRET_KEY.encode()).hexdigest()
        self.assertNotIn(digest, LEAKED_DIGESTS)

    def test_security_headers_are_on(self):
        self.assertTrue(settings.SESSION_COOKIE_HTTPONLY)
        self.assertTrue(settings.CSRF_COOKIE_HTTPONLY)
        self.assertTrue(settings.SECURE_CONTENT_TYPE_NOSNIFF)
        self.assertEqual(settings.X_FRAME_OPTIONS, "DENY")
        self.assertEqual(settings.SECURE_REFERRER_POLICY, "same-origin")

    def test_both_providers_are_configured(self):
        self.assertEqual(sorted(settings.LEDGER_PROVIDERS), ["quickbooks", "xero"])

    def test_default_provider_is_configured(self):
        self.assertIn(default_provider(), settings.LEDGER_PROVIDERS)

    def test_unconfigured_provider_lookup_raises(self):
        with self.assertRaises(UnknownProviderError):
            provider_config("netsuite")


class NoCommittedSecretsTests(SimpleTestCase):
    """Every tracked text file is scanned for the credentials that leaked."""

    def _tracked_files(self) -> list[Path]:
        listing = subprocess.run(
            ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, check=False
        )
        if listing.returncode != 0:  # not a git checkout (e.g. inside a container)
            self.skipTest("not a git working tree")
        return [
            path
            for name in listing.stdout.split()
            if (path := REPO_ROOT / name).is_file()
            and path.suffix not in BINARY_SUFFIXES
        ]

    def test_the_scanner_recognises_a_value_in_its_digest_set(self):
        """Proves the detector works, using a synthetic stand-in.

        A real leaked value is deliberately not written here: the point of
        matching on digests is that no credential need live in the tree at all.
        """
        sample = "sample-credential-value-0123456789"
        digests = frozenset({hashlib.sha256(sample.encode()).hexdigest()})
        self.assertTrue(digests_in(f"SOME_KEY={sample}") & digests)
        self.assertTrue(digests_in(f'SOME_KEY = "{sample}"') & digests)
        self.assertFalse(digests_in("SOME_KEY=") & digests)
        self.assertFalse(digests_in("SOME_KEY=unrelated-but-long-enough") & digests)

    def test_no_leaked_credential_survives_in_the_working_tree(self):
        offenders = []
        for path in self._tracked_files():
            try:
                body = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if digests_in(body) & LEAKED_DIGESTS:
                offenders.append(str(path.relative_to(REPO_ROOT)))
        self.assertEqual(offenders, [], "leaked credentials found in tracked files")

    def test_no_env_file_is_tracked(self):
        tracked = {p.name for p in self._tracked_files()}
        self.assertNotIn(".env", tracked)
        self.assertIn("example.env", tracked)

    def test_example_env_ships_no_values(self):
        body = (REPO_ROOT / "example.env").read_text()
        for line in body.splitlines():
            if line.startswith(("QBOOK_CLIENT", "XERO_CLIENT", "DJANGO_SECRET_KEY")):
                with self.subTest(line=line):
                    self.assertEqual(line.split("=", 1)[1].strip(), "")


class NetworkGuardTests(NoNetworkMixin, SimpleTestCase):
    """Proves the guard the rest of the suite relies on actually bites."""

    def test_opening_a_socket_fails_the_test(self):
        with self.assertRaises(AssertionError) as ctx:
            socket.create_connection(("example.com", 443), timeout=1)
        self.assertIn("attempted a real network connection", str(ctx.exception))

    def test_building_the_shared_transport_opens_nothing(self):
        self.assertIsNotNone(shared_transport())
