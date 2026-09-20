"""Auth header secrets must never reach the log file in clear text.

Regression background: src/requestsV.py logged the entitlements request
headers verbatim at startup, printing the lockfile password
('Basic <base64 of riot:password>') into logs/log-*.txt on every run.
src/logs.redact_headers now keeps the auth scheme and replaces the
credential with '***'. Example: {'Authorization': 'Basic ***'}.
"""
import base64
import os
import re
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from src.logs import Logging, redact_headers  # noqa: E402


def basic_header(password):
    return {'Authorization': 'Basic ' + base64.b64encode(
        ('riot:' + password).encode()).decode()}


class RedactHeadersTest(unittest.TestCase):
    def test_basic_password_is_replaced_with_stars(self):
        headers = basic_header("s3cret-pw")
        self.assertEqual(redact_headers(headers),
                         {'Authorization': 'Basic ***'})

    def test_bearer_token_is_replaced_with_stars(self):
        headers = {'Authorization': 'Bearer eyJabc.def.ghi'}
        self.assertEqual(redact_headers(headers),
                         {'Authorization': 'Bearer ***'})

    def test_non_auth_headers_are_untouched(self):
        headers = {'X-Riot-ClientVersion': 'release-09.00',
                   'User-Agent': 'ShooterGame/13 Windows/10.0.19043.1.256.64bit'}
        self.assertEqual(redact_headers(headers), headers)

    def test_original_dict_is_not_mutated(self):
        headers = basic_header("s3cret-pw")
        redact_headers(headers)
        self.assertTrue(headers['Authorization'].startswith('Basic '))
        self.assertNotIn('***', headers['Authorization'])

    def test_mixed_dict_keeps_other_keys(self):
        headers = basic_header("s3cret-pw")
        headers['X-Riot-ClientPlatform'] = 'ew0KCSJwbGF0Zm9ybVR5cGUi'
        redacted = redact_headers(headers)
        self.assertEqual(redacted['Authorization'], 'Basic ***')
        self.assertEqual(redacted['X-Riot-ClientPlatform'],
                         'ew0KCSJwbGF0Zm9ybVR5cGUi')


class EntitlementsLogLineTest(unittest.TestCase):
    """The startup entitlements line must show the URL but not the password."""

    def setUp(self):
        self._old_cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)
        self.logger = Logging()

    def tearDown(self):
        os.chdir(self._old_cwd)
        self._tmp.cleanup()

    def _log_file_text(self):
        with open(self.logger.logFileName, encoding="utf-8") as handle:
            return handle.read()

    def test_entitlements_log_line_is_redacted(self):
        # Same shape as the get_headers() log line in src/requestsV.py.
        local_headers = basic_header("s3cret-pw")
        self.logger.log(
            f"https://127.0.0.1:2999/entitlements/v1/token\n{redact_headers(local_headers)}")
        text = self._log_file_text()
        credential = base64.b64encode(b"riot:s3cret-pw").decode()
        self.assertNotIn(credential, text)
        self.assertNotIn("s3cret-pw", text)
        self.assertIn("https://127.0.0.1:2999/entitlements/v1/token", text)
        self.assertIn("{'Authorization': 'Basic ***'}", text)


class RequestsVerifyGuardTest(unittest.TestCase):
    """TLS policy for guarded request modules (2026-09-19 w5 + w6a).

    Guarded modules: src/requestsV.py and src/names.py. All their
    requests.* calls are remote (pd.a.pvp.net and friends); the local
    Riot client is only ever called from src/requestsV.py.

    verify=True for ALL remote hosts. verify=False is allowed ONLY on
    request calls whose URL is literally https://127.0.0.1 (the Riot
    client's local API uses a self-signed certificate; verifying it
    breaks startup). Any verify=False reachable via a remote or
    host-variable URL must fail this guard.
    """

    # Every requests verb, so a verify=False cannot hide behind
    # requests.put/post/patch etc. (names.py uses requests.put).
    REQUEST_CALL_RE = re.compile(
        r"requests\.(?:request|get|put|post|patch|delete|head|options)\(")

    def _call_sources(self, source):
        """Yield the source text of every requests.<verb> call."""
        starts = [m.start() for m in self.REQUEST_CALL_RE.finditer(source)]
        for i, start in enumerate(starts):
            end = starts[i + 1] if i + 1 < len(starts) else len(source)
            yield source[start:end]

    def _assert_verify_guard(self, rel_parts, expected_false, expected_true):
        """Enforce the shared TLS policy on one guarded module.

        rel_parts: path segments below the repo root
        (e.g. ("src", "names.py")).
        """
        name = "/".join(rel_parts)
        path = os.path.join(REPO_ROOT, *rel_parts)
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
        calls = list(self._call_sources(source))
        false_calls = [c for c in calls if re.search(r"verify\s*=\s*False", c)]
        for call in false_calls:
            # The URL arguments always precede verify=False, so the text
            # before the flag must pin the local Riot client API host
            # literally (https://127.0.0.1), never a remote or
            # host-variable URL.
            pre_flag = call.split("verify", 1)[0]
            self.assertIn("https://127.0.0.1", pre_flag,
                          "verify=False reachable via a non-local URL in "
                          + name + ": " + pre_flag.strip()[:200])
        # Every remaining request call must verify TLS.
        true_calls = [c for c in calls if re.search(r"verify\s*=\s*True", c)]
        self.assertEqual(len(false_calls), expected_false,
                         name + ": verify=False site count changed; "
                         "re-audit hosts")
        self.assertEqual(len(true_calls), expected_true,
                         name + ": verify=True site count changed; "
                         "re-audit hosts")
        self.assertEqual(len(calls), len(false_calls) + len(true_calls),
                         name + ": request call missing an explicit "
                         "verify= flag")

    def test_verify_false_only_for_literal_127_0_0_1_urls(self):
        # Current audited sites in requestsV.py: the two lockfile-authed
        # local calls (fetch url_type="local" and get_headers entitlements).
        self._assert_verify_guard(("src", "requestsV.py"),
                                  expected_false=2, expected_true=3)

    def test_names_py_remote_calls_verify_tls(self):
        # Current audited sites in names.py: five remote pd.a.pvp.net calls
        # (two name-service PUTs, two match-details GETs, one multi-region
        # name-service PUT), all verifying TLS. names.py must never call
        # the local 127.0.0.1 client API, so zero verify=False is expected.
        self._assert_verify_guard(("src", "names.py"),
                                  expected_false=0, expected_true=5)


if __name__ == "__main__":
    unittest.main()
