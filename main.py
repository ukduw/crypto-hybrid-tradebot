import asyncio
import json
import datetime
import pytz
import traceback
import signal
import aiofiles

import os


from alpaca_utils import start_price_bar_stream, get_current_price, get_bar_data, stop_price_bar_stream, place_order, close_position, crypto_stream

universal = pytz.timezone("UTC")
now = datetime.datetime.now(universal)
exit_open_positions_at = now.replace(hour=23, minute=30, second=0, microsecond=0)


concurrent_trade_counter = 0
concurrent_trade_lock = asyncio.Lock()


CONFIG_PATH = "crypto_configs.json"
last_config_mtime = None
with open("crypto_configs.json", "r") as f:
    cached_configs = json.load(f)

def load_configs_on_modification():
    global last_config_mtime, cached_configs
    try:
        mtime = os.path.getmtime(CONFIG_PATH)
        if mtime != last_config_mtime:
            last_config_mtime = mtime
            with open(CONFIG_PATH, "r") as f:
                cached_configs = json.load(f)
            print("[MOD] Configs updated")
    except Exception as e:
        print(f"[LOOP] Configs mid-modification: {e}")
    return cached_configs
# CAN BE USED IN 24/7 VERSION OF SCRIPT

symbols = [setup["symbol"] for setup in cached_configs]


# needs profit taking logic to allow winners to run, not take profit on first >1.5 pwap
    # in-depth notes from stock script:
        # maybe have series of if statements for different ratio ranges
        # for very high ratios, switch to 5m candle logic and something similar to trail stop
            # e.g. if next 5m bar peaks higher, keep holding?
                # for x higher highs, hold?
                # once it sets lower high by x% OR closes red, exit?
        # otherwise, have condition triggers increment counter?
            # e.g. x bars above condition = y% take profit
            # split take-profit into 3?
                # e.g. 1, 3, 5 counter, 1/3 take-profit per
# profit taking logic needs crypto-specific testing/tweaking...
    # determine whether single take profit is fine or if crypto also needs logic to allow winners to run
    # so far, seems like single take profit is fine?

# needs logic to account for edge case where config is empty (no valid plays)
# don't run script if config empty

# event driven refactor low priority
# write README


async def monitor_trade(setup):
    symbol = setup["symbol"]
    in_position = False

    print(f"[{symbol}] Monitoring... {setup["entry_price"]}, {setup["stop_loss"]}")

    while True:
        configs = load_configs_on_modification()
        updated_setup = next((s for s in configs if s["symbol"] == symbol), None)

        if not updated_setup:
            print(f"[{symbol}] Removed from configs. Stopping thread.")
            return
        
        #if updated_setup != setup:
            #in_position = False
            #setup = updated_setup

        entry = updated_setup["entry_price"]
        stop = updated_setup["stop_loss"]
        qty = round(updated_setup["dollar_value"] / updated_setup["entry_price"])

        price = get_current_price(symbol)
        if price is None:
            await asyncio.sleep(2)
            continue
        # day_high = get_day_high(symbol)
        # if day_high is None:
        #     await asyncio.sleep(2)
        #     continue

        try:
            now = datetime.datetime.now(universal)
            if now >= exit_open_positions_at:
                if in_position:
                    close_position(symbol, qty)
                    print(f"[{symbol}] EOD, Exit @ {price}")
                    async with aiofiles.open("trade-log/crypot_trade_log.txt", "a") as file:
                        await file.write(f"{now}, {symbol}, EOD EXIT, {qty}, {price}" + "\n")

                async with aiofiles.open("trade-log/crypot_trade_log.txt", "a") as file:
                    await file.write("\n")

                await stop_price_bar_stream(symbol)
                return


            if not in_position:
                global concurrent_trade_counter
                if concurrent_trade_counter < 4 and price > entry:
                    async with concurrent_trade_lock:
                        if concurrent_trade_counter < 4:
                            now = datetime.datetime.now(universal).time()
                            if now < datetime.time(23,0): # ~30min before end, tweak
                                # place_order(symbol, qty)
                                print(f"{qty} [{symbol}] BUY @ {price}")
                                in_position = True
                                concurrent_trade_counter += 1
                                async with aiofiles.open("trade-log/crypto_trade_log.txt", "a") as file:
                                    await file.write(f"{now}, {symbol}, ENTRY, {qty}, {price}" + "\n")
                elif not concurrent_trade_counter < 4 and price > entry:
                    print(f"Skipped [{symbol}] @ {price}, PDT limit hit...")
                    await stop_price_bar_stream(symbol)
                    async with aiofiles.open("trade-log/crypto_trade_log.txt", "a") as file:
                        await file.write(f"{now}, {symbol}, skip, {qty}, {price}" + "\n")
                    return

                await asyncio.sleep(1)

            if in_position:
                vwap, high_1m, timestamp_1m = get_bar_data(symbol)

                if price < stop:
                    close_position(symbol, qty)
                    global concurrent_trade_counter
                    async with concurrent_trade_lock:
                        concurrent_trade_counter -= 1
                    print(f"[{symbol}] STOP-LOSS hit. Exiting @ {price}")
                    async with aiofiles.open("trade-log/crypto_trade_log.txt", "a") as file:
                        await file.write(f"{now}, {symbol}, EXIT, {qty}, {price}" + "\n")
                    return

                await asyncio.sleep(1)
                if any(bd is None for bd in [vwap, high_1m, timestamp_1m]):
                    continue

                pwap_ratio = (high_1m/entry - 1) / (high_1m/vwap - 1)
                if pwap_ratio > 1.5: # tweak
                    close_position(symbol, qty)
                    global concurrent_trade_counter
                    async with concurrent_trade_lock:
                        concurrent_trade_counter -= 1
                    print(f"[{symbol}] TAKE-PROFIT hit. Exiting position @ {price}")
                    async with aiofiles.open("trade-log/crypto_trade_log.txt", "a") as file:
                        await file.write(f"{now}, {symbol}, EXIT, {qty}, {price}" + "\n")
                    continue

                while True:
                    vwap2, high_1m2, timestamp_1m2 = get_bar_data(symbol)
                    
                    if any(bd2 is None for bd2 in [vwap2, high_1m2, timestamp_1m2]):
                        continue

                    if timestamp_1m2 != timestamp_1m and not high_1m*0.985 < high_1m2 < high_1m*1.015: # 1.5%, tweak
                        break
                    # may need to change second part of condition; may not apply to most crypto charts...

                    await asyncio.sleep(1)
                    
                await asyncio.sleep(1)


        except Exception as e:
            print(f"[{symbol}] Error: {e}", flush=True)
            # check systemd logs for traceback...
            traceback.print_exc()
            await stop_price_bar_stream(symbol)
        

        await asyncio.sleep(1)


async def supervisor(coro_func, *args, name="task"):
    # while True: # looping to restart after 5s risks getting stuck in an enter, fail, enter, fail... loop - needs global variables
        try:
            await coro_func(*args)
        except Exception as e:
            print(f"{name} crashed: {e}")
            # await asyncio.sleep(5)

async def main():
    try:
        data_stream_task = asyncio.create_task(supervisor(start_price_bar_stream, symbols, name="data_stream"))
        monitor_tasks = [
            asyncio.create_task(
                supervisor(monitor_trade, setup, name=f"monitor_trade-{setup['symbol']}")
            ) 
            for setup in cached_configs
        ]
        await asyncio.gather(data_stream_task, *monitor_tasks)
    except asyncio.CancelledError:
        print("Error, tasks cancelled")
    finally:
        await handle_shutdown()

# systemctl stop / ctrl+c cleanup
async def handle_shutdown():
    print("Shutting down...")

    for symbol in symbols:
        await stop_price_bar_stream(symbol)
    await crypto_stream.stop_ws()

    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    print("Cleanup complete. Exiting...")


def main_start():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(handle_shutdown()))

    try:
        loop.run_until_complete(main())
    finally:
        loop.close()
        print("Event loop closed")


if __name__ == "__main__":
    main_start()

