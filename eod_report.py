import time
import requests
import pytz
import datetime

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
while pb_reconnect_tries < 5:
    try:
        pb = Pushbullet(PB_API_KEY)
        break
    except requests.exceptions.ConnectionError as e:
        pb_reconnect_tries += 1
        print(f"PB connection failed({pb_reconnect_tries}/5), retrying in 10s...", e)
        time.sleep(10)


universal = pytz.timezone("UTC")
now = datetime.datetime.now(universal)


# script to calculate and summarize daily p/l
    # read crypto_trade_log.txt
    # compare datetime DATE with now; if ==, if "ENTRY", if "EXIT"
    # get prices
    # calculate % difference, total, with below format
    # include total count of trades?

# pushbullet noti
# write to log - needs systemd service/timer
    # run at 23:30 (UTC)?

# format e.g. +5.0%, -1.6%, +3.4%...
    # total (additive): x%

