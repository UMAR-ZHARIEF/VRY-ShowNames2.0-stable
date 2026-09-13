import base64
import json
import time

class Presences:
    def __init__(self, Requests, log):
        self.Requests = Requests
        self.log = log

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
                    decoded_private = json.loads(base64.b64decode(presence['private']))
                    # Debug
                    # self.log(f"DEBUG: Decoded Private Presence -> {decoded_private}")
                    return decoded_private
        return None

    def decode_presence(self, private):
        # try:
        if "{" not in str(private) and private is not None and str(private) != "":
            dict = json.loads(base64.b64decode(str(private)).decode("utf-8"))
            if dict.get("isValid"):
                return dict
        return {
            "isValid": False,
            "partyId": 0,
            "partySize": 0,
            "partyVersion": 0,
        }

    def wait_for_presence(self, PlayersPuuids, timeout_seconds=10):
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