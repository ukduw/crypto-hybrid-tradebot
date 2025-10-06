from alpaca.data.live import CryptoDataStream
from alpaca.data.enums import CryptoFeed

from dotenv import load_dotenv
import os
import json

import asyncio

from alpaca_utils import stop_price_bar_stream

load_dotenv()
API_KEY = os.getenv("API_KEY")
SECRET_KEY = os.getenv("SECRET_KEY")

crypto_stream = CryptoDataStream(api_key=API_KEY, secret_key=SECRET_KEY, feed=CryptoFeed.US)


CONFIG_PATH = "configs.json"
with open("configs.json", "r") as f:
    configs_json = json.load(f)

symbols = [setup["symbol"] for setup in configs_json]


async def main():
    print("Running systemd cleanup...")

    for symbol in symbols:
        await stop_price_bar_stream(symbol)
    await crypto_stream.stop_ws()

    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    print("Cleanup complete. Exiting...")
    

if __name__ == "__main__":
    asyncio.run(main())


