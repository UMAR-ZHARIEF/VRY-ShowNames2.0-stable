import base64
import binascii
import json
import time

class Presences:
    def __init__(self, Requests, log):
        self.Requests = Requests
        self.log = log
        self._decode_failure_count = 0

    def _log_decode_failure(self, site, detail):
        """Rate-limited presence-decode failure log: first failure, then
        every 50th, so a poison blob cannot spam the log."""
        self._decode_failure_count += 1
        if self._decode_failure_count == 1 or self._decode_failure_count % 50 == 0:
            try:
                self.log(f"presence decode failure #{self._decode_failure_count} ({site}): {detail}")
            except Exception:
                pass

    def _decode_private_payload(self, raw, site):
        """Decode a presence 'private' blob (base64 of JSON) into a dict.

        The client sometimes delivers the blob DOUBLE-ENCODED: base64 of a
        JSON string that itself contains the JSON object, so a str result is
        json-parsed once more. Returns the dict, or None on any failure
        (bad base64, malformed JSON, non-dict payload); failures are logged
        rate-limited and never raised.
        """
        try:
            payload = json.loads(base64.b64decode(str(raw)).decode("utf-8"))
            if isinstance(payload, str):
                # Double-encoded payload: unwrap the inner JSON string.
                payload = json.loads(payload)
            if not isinstance(payload, dict):
                self._log_decode_failure(site, f"payload is {type(payload).__name__}, not a dict")
                return None
            return payload
        except (json.JSONDecodeError, ValueError, TypeError, AttributeError, binascii.Error) as exc:
            self._log_decode_failure(site, f"{type(exc).__name__}: {exc}")
            return None

    def get_presence(self):
        presences = self.Requests.fetch(url_type="local", endpoint="/chat/v4/presences", method="get")
        if presences is None:
            return None
        return presences['presences']

    def get_game_state(self, presences):
        private_presence = self.get_private_presence(presences)
        if private_presence:
            # Temp fix: Riot is swapping between nested and flat API structures.
            # Check for nested structure.
            if "matchPresenceData" in private_presence:
                return private_presence["matchPresenceData"]["sessionLoopState"]
            # Check for flattened structure.
            elif "sessionLoopState" in private_presence:
                return private_presence["sessionLoopState"]
            else:
                # No known structure found, log and fail
                self.log("ERROR: Unknown presence API structure in 'get_game_state'.")
                return private_presence["matchPresenceData"]["sessionLoopState"]
        return None

    def get_private_presence(self, presences):
        for presence in presences:
            if presence['puuid'] == self.Requests.puuid:
                #preventing vry from crashing when lol is open
                # print(presence)
                # print(presence.get("championId"))
                if presence.get("championId") is not None or presence.get("product") == "league_of_legends":
                    return None
                else:
                    if presence['private'] == "": 
                        return None
                    # Double-encode tolerant decode: None (rate-limited log)
                    # instead of an exception on malformed blobs.
                    # Debug
                    # self.log(f"DEBUG: Decoded Private Presence -> {decoded_private}")
                    return self._decode_private_payload(presence['private'], "get_private_presence")
        return None

    def decode_presence(self, private):
        if "{" not in str(private) and private is not None and str(private) != "":
            payload = self._decode_private_payload(private, "decode_presence")
            if payload is not None and payload.get("isValid"):
                return payload
        return {
            "isValid": False,
            "partyId": 0,
            "partySize": 0,
            "partyVersion": 0,
        }

    def wait_for_presence(self, PlayersPuuids, timeout_seconds=2):
        """Poll until every puuid in PlayersPuuids appears in /chat/v4/presences,
        or timeout. Returns True on success, False on timeout.
        """
        start = time.time()
        missing = list(PlayersPuuids)
        while time.time() - start < timeout_seconds:
            presence = self.get_presence()
            if presence is not None:
                presence_str = str(presence)
                missing = [p for p in PlayersPuuids if p not in presence_str]
                if not missing:
                    return True
            time.sleep(0.5)
        try:
            self.log(f"wait_for_presence: timeout, {len(missing)} puuids still missing")
        except Exception:
            pass
        return False