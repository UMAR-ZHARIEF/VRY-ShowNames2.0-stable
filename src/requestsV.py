import base64
import json
import threading
import time
import os
from requests.exceptions import ConnectionError

from src.http_pool import pooled_requests
from src.logs import redact_headers

# All HTTP from this module goes through the shared keep-alive pool in
# src/http_pool.py, bound under the familiar name so every call site below
# keeps its exact shape and its explicit per-request verify= flag (True on
# all remote hosts, False only on the literal https://127.0.0.1 client API;
# guard-tested in tests/test_secrets_redaction.py).
requests = pooled_requests


def _parse_json_body(response):
    """Parse a response body as JSON; None means empty or non-JSON body.

    A literal "null" body also maps to None (json.loads("null") is None),
    so one sentinel covers every body fetch() cannot use as a dict. This is
    the None the old JSONDecodeError handler already returned for empty
    bodies; it just no longer crashes on "null" bodies first.
    """
    try:
        return response.json()
    except ValueError:
        return None

class Requests:
    def __init__(self, version, log, Error):
        self.Error = Error
        self.version = version
        self.headers = {}
        # Serializes the header refresh/mutation path so parallel fetches
        # can neither fire duplicate entitlements calls nor observe a
        # half-updated header dict; see get_headers.
        self._headers_lock = threading.Lock()
        self.log = log


        self.lockfile = self.get_lockfile()
        self.region = self.get_region()
        self.pd_url = f"https://pd.{self.region[0]}.a.pvp.net"
        self.glz_url = f"https://glz-{self.region[1][0]}.{self.region[1][1]}.a.pvp.net"
        self.log(f"Api urls: pd_url: '{self.pd_url}', glz_url: '{self.glz_url}'")
        self.region = self.region[0]
        
        self.puuid = ''
        #fetch puuid so its avaible outside
        if not self.get_headers(init=True):
            self.log("Invalid URI format, invalid lockfile, going back to menu")
            self.get_lockfile(ignoreLockfile=True)
               
    def fetch(self, url_type: str, endpoint: str, method: str, rate_limit_seconds=5):
        try:
            if url_type == "glz":
                response = requests.request(method, self.glz_url + endpoint, headers=self.get_headers(), verify=True)
                self.log(f"fetch: url: '{url_type}', endpoint: {endpoint}, method: {method},"
                    f" response code: {response.status_code}")

                body = _parse_json_body(response)
                if body is None:
                    # Empty/non-JSON body (seen in production: 200 with an
                    # empty body at a match boundary): one compact log line,
                    # no BAD_CLAIMS check, no retry recursion.
                    self.log(f"fetch: url: '{url_type}', endpoint: {endpoint},"
                             f" status code {response.status_code}: empty/non-JSON body, skipping")
                    return None

                if response.status_code == 404:
                    return body

                if isinstance(body, dict) and body.get("errorCode") == "BAD_CLAIMS":
                    self.log("detected bad claims")
                    self.headers = {}
                    return self.fetch(url_type, endpoint, method)
                if not response.ok:
                    if response.status_code == 429:
                        self.log("response not ok glz endpoint: rate limit 429")
                    else:
                        self.log("response not ok glz endpoint: " + response.text)
                    time.sleep(rate_limit_seconds+5)
                    self.headers = {}
                    self.fetch(url_type, endpoint, method)
                return body
            elif url_type == "pd":
                response = requests.request(method, self.pd_url + endpoint, headers=self.get_headers(), verify=True)
                self.log(
                    f"fetch: url: '{url_type}', endpoint: {endpoint}, method: {method},"
                    f" response code: {response.status_code}")
                if response.status_code == 404:
                    return response

                body = _parse_json_body(response)
                if body is None:
                    # pd fetch returns the Response object itself and callers
                    # parse it, so an empty body only gets the compact log
                    # line; control flow is unchanged.
                    self.log(f"fetch: url: '{url_type}', endpoint: {endpoint},"
                             f" status code {response.status_code}: empty/non-JSON body, skipping")
                elif isinstance(body, dict) and body.get("errorCode") == "BAD_CLAIMS":
                    self.log("detected bad claims")
                    self.headers = {}
                    return self.fetch(url_type, endpoint, method)

                if not response.ok:
                    if response.status_code == 429:
                        self.log(f"response not ok pd endpoint, rate limit 429")
                    else:
                        self.log(f"response not ok pd endpoint, {response.text}")
                    time.sleep(rate_limit_seconds+5)
                    self.headers = {}
                    return self.fetch(url_type, endpoint, method, rate_limit_seconds=rate_limit_seconds+5)
                return response
            elif url_type == "local":
                local_headers = {'Authorization': 'Basic ' + base64.b64encode(
                    ('riot:' + self.lockfile['password']).encode()).decode()}
                
                max_retries = 3
                for i in range(max_retries):
                    try:
                        response = requests.request(method, f"https://127.0.0.1:{self.lockfile['port']}{endpoint}",
                                                    headers=local_headers,
                                                    # 127.0.0.1 only: the Riot client's local API uses a self-signed cert.
                                                    verify=False, timeout=5)
                        body = _parse_json_body(response)
                        if response.status_code == 200 and isinstance(body, dict) and body.get("errorCode") != "RPC_ERROR":
                            if endpoint != "/chat/v4/presences":
                                self.log(
                                    f"fetch: url: '{url_type}', endpoint: {endpoint}, method: {method},"
                                    f" response code: {response.status_code}")
                            return response.json()
                        else:
                            if body is None:
                                self.log(f"fetch: url: 'local', endpoint: {endpoint},"
                                         f" status code {response.status_code}: empty/non-JSON body, skipping")
                            else:
                                self.log(f"Local API is not ready yet (RPC_ERROR or status code {response.status_code}). Retrying...")
                            time.sleep(5)
                    except (requests.exceptions.RequestException, ConnectionError):
                        self.log(f"Connection error on local request. Retrying... ({i + 1}/{max_retries})")
                        time.sleep(5)
                
                self.log(f"Failed to connect to local client after {max_retries} attempts.")
                return None
            elif url_type == "custom":
                response = requests.request(method, f"{endpoint}", headers=self.get_headers(), verify=True)
                self.log(
                    f"fetch: url: '{url_type}', endpoint: {endpoint}, method: {method},"
                    f" response code: {response.status_code}")
                if not response.ok: self.headers = {}
                body = _parse_json_body(response)
                if body is None:
                    self.log(f"fetch: url: '{url_type}', endpoint: {endpoint},"
                             f" status code {response.status_code}: empty/non-JSON body, skipping")
                    return None
                return body
        except json.decoder.JSONDecodeError:
            self.log(
                f"JSONDecodeError in fetch function, resp.code: {response.status_code}, "
                f"full response: {response!r}, full response text: {response.text}"
            )
            print(response)
            print(response.text)

    def get_region(self):
        path = os.path.join(os.getenv('LOCALAPPDATA'), R'VALORANT\Saved\Logs\ShooterGame.log')
        with open(path, "r", encoding="utf8") as file:
            while True:
                line = file.readline()
                if '.a.pvp.net/account-xp/v1/' in line:
                    pd_url = line.split('.a.pvp.net/account-xp/v1/')[0].split('.')[-1]
                elif 'https://glz' in line:
                    glz_url = [(line.split('https://glz-')[1].split(".")[0]),
                               (line.split('https://glz-')[1].split(".")[1])]
                if "pd_url" in locals().keys() and "glz_url" in locals().keys():
                    self.log(f"got region from logs '{[pd_url, glz_url]}'")
                    if pd_url == "pbe":
                        return ["na", "na-1", "na"]
                    return [pd_url, glz_url]

    def get_current_version(self):
        path = os.path.join(os.getenv('LOCALAPPDATA'), R'VALORANT\Saved\Logs\ShooterGame.log')
        with open(path, "r", encoding="utf8") as file:
            while True:
                line = file.readline()
                if 'CI server version:' in line:
                    version_without_shipping = line.split('CI server version: ')[1].strip()
                    version = version_without_shipping.split("-")
                    version = "-".join(version)
                    self.log(f"got version from logs '{version}'")
                    return version

    def get_lockfile(self, ignoreLockfile=False):
        #ignoring lockfile is for when lockfile exists but it's not really valid, (local endpoints are not initialized yet)
        path = os.path.join(os.getenv('LOCALAPPDATA'), R'Riot Games\Riot Client\Config\lockfile')
        
        if self.Error.LockfileError(path, ignoreLockfile=ignoreLockfile):
            with open(path) as lockfile:
                self.log("opened lockfile")
                data = lockfile.read().split(':')
                keys = ['name', 'PID', 'port', 'password', 'protocol']
                return dict(zip(keys, data))


    def get_headers(self, refresh=False, init=False):
        # Single-flight under the lock: parallel fetches hitting an empty
        # or expired cache must not fire duplicate entitlements calls, and
        # no caller may observe a partially built header dict. The lock is
        # held only while the cache is empty or a refresh is forced; the
        # steady-state path is a dict check plus returning the complete
        # current dict, so parallel fetches do not serialize on it.
        with self._headers_lock:
            if self.headers == {} or refresh:
                try_again = True
                while try_again:
                    local_headers = {'Authorization': 'Basic ' + base64.b64encode(
                        ('riot:' + self.lockfile['password']).encode()).decode()}
                    try:
                        response = requests.get(f"https://127.0.0.1:{self.lockfile['port']}/entitlements/v1/token",
                                                # 127.0.0.1 only: the Riot client's local API uses a self-signed cert.
                                                headers=local_headers, verify=False)
                        self.log(f"https://127.0.0.1:{self.lockfile['port']}/entitlements/v1/token\n{redact_headers(local_headers)}")
                    except ConnectionError:
                        self.log(f"https://127.0.0.1:{self.lockfile['port']}/entitlements/v1/token\n{redact_headers(local_headers)}")
                        self.log("Connection error, retrying in 1 seconds, getting new lockfile")
                        time.sleep(1)
                        self.lockfile = self.get_lockfile()
                        continue
                    entitlements = response.json()
                    if entitlements.get("message") == "Entitlements token is not ready yet":
                        try_again = True
                        time.sleep(1)
                    elif entitlements.get("message") == "Invalid URI format":
                        self.log(f"Invalid uri format: {entitlements}")
                        if init:
                            return False
                        else:
                            try_again = True
                            time.sleep(5)
                    else:
                        try_again = False

                self.puuid = entitlements['subject']
                headers = {
                    'Authorization': f"Bearer {entitlements['accessToken']}",
                    'X-Riot-Entitlements-JWT': entitlements['token'],
                    'X-Riot-ClientPlatform': "ew0KCSJwbGF0Zm9ybVR5cGUiOiAiUEMiLA0KCSJwbGF0Zm9ybU9TIjog"
                                             "IldpbmRvd3MiLA0KCSJwbGF0Zm9ybU9TVmVyc2lvbiI6ICIxMC4wLjE5"
                                             "MDQyLjEuMjU2LjY0Yml0IiwNCgkicGxhdGZvcm1DaGlwc2V0IjogIlVua25vd24iDQp9",
                    'X-Riot-ClientVersion': self.get_current_version(),
                    "User-Agent": "ShooterGame/13 Windows/10.0.19043.1.256.64bit"
                }
                self.headers = headers
            return self.headers
