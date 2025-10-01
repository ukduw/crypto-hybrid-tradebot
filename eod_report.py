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


# write to log - needs systemd service/timer
    # run at 23:30 (UTC)?

# intput format:
    # "{now}, {coin}, ENTRY/EXIT/skip/EOD EXIT, {qty}, {price}"
    # ...

def daily_pl_calc():
    # calculate % difference, total, with below format
    # include total count of trades?

    entry_exit = {}
    percs = []

    universal = pytz.timezone("UTC")
    now = datetime.datetime.now(universal)
    today = now.date()

    with open("trade-log/crypto_trade_log.txt", "a") as file:
        lines = file.readlines()

    for l in lines:
        split_line = l.split(", ")
        split_line[0] = datetime.datetime.strptime(split_line[0].strip(), "%Y-%m-%d %H:%M:%S")
        
        if split_line[0].date() == today:
            if "ENTRY" in split_line[2]:
                entry_exit[split_line[1]] = [float(split_line[4])]
            if "EXIT" in split_line[2]:
                entry_exit[split_line[1]].append(float(split_line[4]))





    # pushbullet noti
    pb.push_note("title", "message") # placeholder
    print("message") # placeholder


# output format:
    # +5.0%, -1.6%, +3.4%...
    # total (additive): x%


if __name__ == "__main__":
    daily_pl_calc()

