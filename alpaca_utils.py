from alpaca.data.live import CryptoDataStream
from alpaca.data.models import Trade, Bar
from alpaca.data.enums import DataFeed

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce, OrderType

import datetime, pytz, asyncio, aiofiles
from decimal import Decimal, ROUND_UP, ROUND_DOWN
from collections import defaultdict, deque
from dataclasses import dataclass

from dotenv import load_dotenv
import os
import json

import tracemalloc
tracemalloc.start()

load_dotenv()
API_KEY = os.getenv("API_KEY")
SECRET_KEY = os.getenv("SECRET_KEY")
USE_PAPER_TRADING = os.getenv("USE_PAPER_TRADING")

CONFIG_PATH = "configs.json"
with open("configs.json", "r") as f:
    configs = json.load(f)

gap_up_first_tick = {}
gap_counter = {}
last_tick = {}
tick_counter = {}

latest_prices = {}

vwaps = {}
latest_highs = {}
latest_timestamps = {}

eastern = pytz.timezone("US/Eastern")
now = datetime.datetime.now(eastern)

trading_client = TradingClient(api_key=API_KEY, secret_key=SECRET_KEY, paper=USE_PAPER_TRADING)
crypto_stream = CryptoDataStream(api_key=API_KEY, secret_key=SECRET_KEY, feed=DataFeed.Crypto)

# REFACTOR FOR CRYPTO
# replace api calls
# update handlers
# remove gap up protection
# fix/replace trading utils; not need for extended/intraday logic
# change timezone to UTC


# ===== WEBSOCKETS, DATA STREAM HANDLERS ===== #
@dataclass
class BarEntry:
    open: float
    high: float
    low: float
    close: float
    volume: int
    vwap: float
    timestamp: datetime.datetime

class DataHandler:
    def __init__(self):
        self.quote_window = defaultdict(lambda: deque(maxlen=500))
        self.bar_window = defaultdict(lambda: deque(maxlen=5))

    async def handle_trade(self, trade: Trade):
        symbol = trade.symbol
        trade_price = trade.price
        setup = next((s for s in configs if s["symbol"] == symbol), None)
        entry = setup["entry_price"]
        exit = setup["stop_loss"]
        now = datetime.datetime.now(eastern)

        if trade.size < 100:
            async with aiofiles.open(f"price-stream-logs/price_stream_log_{trade.symbol}.txt", "a") as file:
                await file.write(f"[ODD LOT] {now},{trade.symbol},PRICE {trade.price},VOL {trade.size}, COND {trade.conditions}" + "\n")
            return
        
        if trade_price > entry:
            if symbol not in gap_up_first_tick:
                gap_up_first_tick[symbol] = trade_price
                gap_counter[symbol] = 0
            elif symbol not in last_tick:
                last_tick[symbol] = trade_price
                tick_counter[symbol] = 0

            if gap_up_first_tick[symbol] > entry:
                if trade_price > gap_up_first_tick[symbol] * 1.015 or trade_price <= exit: # 1.5%, TWEAK
                    latest_prices[symbol] = trade_price
                    async with aiofiles.open(f"price-stream-logs/price_stream_log_{trade.symbol}.txt", "a") as file:
                        await file.write(f"[GAP UP] {now},{trade.symbol},PRICE {trade.price},VOL {trade.size}, COND {trade.conditions}" + "\n")
                else:
                    gap_counter[symbol] += 1
                    async with aiofiles.open(f"price-stream-logs/price_stream_log_{trade.symbol}.txt", "a") as file:
                        await file.write(f"[GAP UP - {gap_counter[symbol]}/100] {now},{trade.symbol},PRICE {trade.price},VOL {trade.size}, COND {trade.conditions}" + "\n")

                if gap_counter[symbol] >= 100: # 100 consolidation ticks, TWEAK
                    gap_up_first_tick[symbol] = 0 # only tracked once, then last_tick tracked instead
                    async with aiofiles.open(f"price-stream-logs/price_stream_log_{trade.symbol}.txt", "a") as file:
                        await file.write(f"[GAP UP MONITORING ENDED] {now},{trade.symbol},PRICE {trade.price},VOL {trade.size}, COND {trade.conditions}" + "\n")
            else:
                if exit < trade_price < last_tick[symbol]:
                    tick_counter[symbol] += 1
                    async with aiofiles.open(f"price-stream-logs/price_stream_log_{trade.symbol}.txt", "a") as file:
                        await file.write(f"[>ENTRY - {tick_counter[symbol]}/50] {now},{trade.symbol},PRICE {trade.price},VOL {trade.size}, COND {trade.conditions}" + "\n")
                else:
                    latest_prices[symbol] = trade_price
                    async with aiofiles.open(f"price-stream-logs/price_stream_log_{trade.symbol}.txt", "a") as file:
                        await file.write(f"[CONFIRMED TICK] {now},{trade.symbol},PRICE {trade.price},VOL {trade.size}, COND {trade.conditions}" + "\n")

                if tick_counter[symbol] >= 50:
                    last_tick.pop(symbol)
                    async with aiofiles.open(f"price-stream-logs/price_stream_log_{trade.symbol}.txt", "a") as file:
                        await file.write(f"[>ENTRY MONITORING ENDED] {now},{trade.symbol},PRICE {trade.price},VOL {trade.size}, COND {trade.conditions}" + "\n")
        elif trade_price <= exit:
            latest_prices[symbol] = trade_price
            async with aiofiles.open(f"price-stream-logs/price_stream_log_{trade.symbol}.txt", "a") as file:
                await file.write(f"[AROUND EXIT] {now},{trade.symbol},PRICE {trade.price},VOL {trade.size}, COND {trade.conditions}" + "\n")


    async def handle_bar(self, bar: Bar): 
        self.bar_window[bar.symbol].append(
            BarEntry(
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
                vwap=bar.vwap,
                timestamp=bar.timestamp
            )
        )
        vwaps.setdefault(bar.symbol, []).append(bar.vwap)

        last_entry = self.bar_window[bar.symbol][-1]
        latest_highs[bar.symbol] = last_entry.high
        latest_timestamps[bar.symbol] = last_entry.timestamp 


# ===== OPEN/CLOSE STREAM, HANDLER CALL UTILS ===== #
handler = DataHandler()

async def start_price_bar_stream(symbols):
    retries = 0
    while True:
        try:
            for symbol in symbols:
                crypto_stream.subscribe_trades(handler.handle_trade, symbol)
                crypto_stream.subscribe_bars(handler.handle_bar, symbol)
            
            await crypto_stream._run_forever()
        except asyncio.CancelledError:
            print("[WebSocket] Cancelled")
            raise
        except Exception as e:
            retries += 1
            print(f"[WebSocket] Crash {retries}: {e}")

            if retries >= 20:
                print("[WebSocket] Too many retries, giving up...")
                raise # lets outer supervisor handle shutdown
            else:
                print(f"[WebSocket] Stream reconnect attempt in 15 seconds...")
                await asyncio.sleep(15)

        else: # in case of normal exit
            print("[WebSocket] Stopped gracefully")
            break

async def stop_price_bar_stream(symbol):
    try:
        crypto_stream.unsubscribe_trades(symbol)
        crypto_stream.unsubscribe_bars(symbol)
        print(f"[{symbol}] price/quote stream unsubscribed")
    except Exception as e:
        print (f"[WebSocket] Error unsubscribing from {symbol}: {e}")


# ===== VALUE RETRIEVAL UTILS (to main) ===== #
def get_current_price(symbol):
    return latest_prices.get(symbol)

def get_bar_data(symbol):
    vwap_list = vwaps.get(symbol)
    if vwap_list is None:
        vwap = None
    else:
        vwap = vwap_list[-1]

    #stdev = vwap_stdevs.get(symbol)
    #close_5m = latest_5m_closes.get(symbol)
    high = latest_highs.get(symbol)
    timestamp = latest_timestamps.get(symbol)

    return vwap, high, timestamp


# ===== TRADING CLIENT UTILS ===== #
def place_order(symbol, qty):
    intraday = is_intraday()
    tick = get_current_price(symbol)

    if intraday:
        order_data = MarketOrderRequest(
            symbol = symbol,
            qty = qty,
            side = OrderSide.BUY,
            type = OrderType.MARKET,
            time_in_force = TimeInForce.DAY,
            extended_hours = False
        )
    else:
        order_data = LimitOrderRequest(
            symbol = symbol,
            qty = qty,
            side = OrderSide.BUY,
            type = OrderType.LIMIT,
            time_in_force = TimeInForce.DAY,
            limit_price = float(Decimal(tick * 1.01).quantize(Decimal("0.01"), rounding=ROUND_UP)) if tick >= 1.00 else float(Decimal(tick * 1.01).quantize(Decimal("0.0001"), rounding=ROUND_UP)),
            extended_hours = True
        )
    order = trading_client.submit_order(order_data)
    return order


def close_position(symbol, qty):
    intraday = is_intraday()
    tick = get_current_price(symbol)

    if not intraday:
        order_data = LimitOrderRequest(
            symbol = symbol,
            qty = qty,
            side = OrderSide.SELL,
            type = OrderType.LIMIT,
            time_in_force = TimeInForce.DAY,
            limit_price = float(Decimal(tick * 0.99).quantize(Decimal("0.01"), rounding=ROUND_DOWN)) if tick >= 1.00 else float(Decimal(tick * 0.99).quantize(Decimal("0.0001"), rounding=ROUND_DOWN)),
            extended_hours = True
        )
        order = trading_client.submit_order(order_data)
        return order
    else:
        return trading_client.close_position(symbol, qty)

