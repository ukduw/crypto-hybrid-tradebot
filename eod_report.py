# script to calculate and summarize daily p/l
# write to log - needs systemd service/timer
    # run at 23:30 (UTC)?
# pushbullet noti

# e.g. +5.0%, -1.6%, +3.4%...
    # total (additive): x%


import time
import requests

from pushbullet import Pushbullet

from dotenv import load_dotenv
import os

load_dotenv()

PB_API_KEY = os.getenv("PUSHBULLET_API_KEY")

class DummyPB:
    def push_note(self, title, body):
        print(f"(PB Failed) Unsent notification: {title}, {body}")

pb = DummyPB()

pb_reconnect_tries = 0
while pb_reconnect_tries < 5: # low due to risk of getting stuck in loop past premarket open...
    try:
        pb = Pushbullet(PB_API_KEY)
        break
    except requests.exceptions.ConnectionError as e:
        pb_reconnect_tries += 1
        print(f"PB connection failed({pb_reconnect_tries}/5), retrying in 10s...", e)
        time.sleep(10)