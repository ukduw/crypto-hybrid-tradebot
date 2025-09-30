from alpaca.data.live import StockDataStream
from alpaca.data.models import Trade, Quote, Bar
from alpaca.data.enums import DataFeed

from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

import pandas as pd
import pandas_ta as ta
import statistics

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.requests import LimitOrderRequest
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
day_high = {}

latest_macd = {}
latest_rsi = {}

vwaps = {}
latest_highs = {}
latest_timestamps = {}

eastern = pytz.timezone("US/Eastern")
now = datetime.datetime.now(eastern)

historical_client = StockHistoricalDataClient(api_key=API_KEY, secret_key=SECRET_KEY)
trading_client = TradingClient(api_key=API_KEY, secret_key=SECRET_KEY, paper=USE_PAPER_TRADING)
stock_stream = StockDataStream(api_key=API_KEY, secret_key=SECRET_KEY, feed=DataFeed.SIP)


# ===== WEBSOCKETS, DATA STREAM HANDLERS + MACD CALC ===== #
@dataclass
class QuoteEntry:
    bid: float
    ask: float
    timestamp: datetime.datetime

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
            # consider getting rid of deque altogether...
            # and computing EMAs incrementally, manually... (without pandas-ta)

    async def handle_quote(self, quote: Quote):
        self.quote_window[quote.symbol].append(
            QuoteEntry(
                bid=quote.bid_price,
                ask=quote.ask_price,
                timestamp=quote.timestamp
            )
        )

    async def handle_trade(self, trade: Trade):
        symbol = trade.symbol
        trade_time = trade.timestamp
        trade_price = trade.price
        setup = next((s for s in configs if s["symbol"] == symbol), None)
        entry = setup["entry_price"]
        exit = setup["stop_loss"]
        now = datetime.datetime.now(eastern)

        #quotes: deque[QuoteEntry] = self.quote_window[symbol]
        #if not quotes:
        #    async with aiofiles.open(f"price-stream-logs/price_stream_log_{trade.symbol}.txt", "a") as file:
        #        await file.write(f"[GHOST no quotes] {now},{trade.symbol},PRICE {trade.price},VOL {trade.size}, COND {trade.conditions}" + "\n")
        #    return
        
        #closest_quote = min(quotes, key=lambda q: abs((q.timestamp - trade_time).total_seconds()))
        #if abs((closest_quote.timestamp - trade_time).total_seconds()) > 1:
        #    async with aiofiles.open(f"price-stream-logs/price_stream_log_{trade.symbol}.txt", "a") as file:
        #        await file.write(f"[GHOST >1sec gap] {now},{trade.symbol},PRICE {trade.price},VOL {trade.size}, COND {trade.conditions}" + "\n")
        #    return  
        
        #tolerance = 0.02 # 2.0%
        #if not (closest_quote.bid * (1 - tolerance) <= trade_price <= closest_quote.ask * (1 - tolerance)):
        #    async with aiofiles.open(f"price-stream-logs/price_stream_log_{trade.symbol}.txt", "a") as file:
        #        await file.write(f"[GHOST >2% price diff] {now},{trade.symbol},PRICE {trade.price},VOL {trade.size}, COND {trade.conditions}" + "\n")
        #    return

        if trade.size < 100:
            async with aiofiles.open(f"price-stream-logs/price_stream_log_{trade.symbol}.txt", "a") as file:
                await file.write(f"[ODD LOT] {now},{trade.symbol},PRICE {trade.price},VOL {trade.size}, COND {trade.conditions}" + "\n")
            return
        
        # if all conditions pass:
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
                    if symbol not in day_high or trade_price > day_high[symbol]:
                        day_high[symbol] = trade_price
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
                    if symbol not in day_high or trade_price > day_high[symbol]:
                        day_high[symbol] = trade_price
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

        
        #else:
        #    latest_prices[symbol] = trade_price
        #    if symbol not in day_high or trade_price > day_high[symbol]:
        #        day_high[symbol] = trade_price

        # print(f"[WebSocket] {trade.symbol} @ {trade.price}") # comment out while not testing

        #async with aiofiles.open(f"price-stream-logs/price_stream_log_{trade.symbol}.txt", "a") as file:
        #    await file.write(f"{now},{trade.symbol},PRICE {trade.price},VOL {trade.size}, COND {trade.conditions}" + "\n")

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
        # bars = list(self.bar_window[bar.symbol])
        # latest_macd[bar.symbol] = self.compute_macd(pd.DataFrame([b.__dict__ for b in bars]))
        # latest_rsi[bar.symbol] = self.compute_rsi(pd.DataFrame([b.__dict__ for b in bars]))

        vwaps.setdefault(bar.symbol, []).append(bar.vwap)

        # VWAP STDEV (NOT IN USE):
        #if len(vwaps[bar.symbol]) > 1:
        #    vwap_stdevs[bar.symbol] = statistics.stdev(vwaps[bar.symbol])

        # 5MIN BARS (NOT IN USE):
        #if len(self.bar_window[bar.symbol]) == 5:
        #    last_entry = self.bar_window[bar.symbol][-1]
        #    latest_5m_closes[bar.symbol] = last_entry.close

        #    bar_highs = []
        #    for bar_1m in self.bar_window[bar.symbol]:
        #        bar_highs.append(bar_1m.high)
        #    latest_5m_highs[bar.symbol] = max(bar_highs)

        #    latest_5m_timestamps[bar.symbol] = last_entry.timestamp
        #    self.bar_window[bar.symbol].clear()

        last_entry = self.bar_window[bar.symbol][-1]
        latest_highs[bar.symbol] = last_entry.high
        latest_timestamps[bar.symbol] = last_entry.timestamp


    
    async def seed_history_recalc_on_bar(self, symbol):
        lookback_bars = 20 # 100 for macd, 20 for rsi
        lookback_minutes = lookback_bars * 15
        now = datetime.datetime.now(eastern)
        start_time = now - datetime.timedelta(minutes=lookback_minutes)
        after_first_bar = now.replace(hour=4, minute=14, second=0, microsecond=0)

        request_params = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame(5, TimeFrame.Minute),
            start=start_time,
            end=now,
            adjustment="raw",
            feed="sip"
        )

        bars = historical_client.get_stock_bars(request_params).df
        if isinstance(bars.index, pd.MultiIndex):
            df = bars.xs(symbol, level=0).sort_index()
        else:
            df = bars.sort_index()
    
        # NOTE: bar timestamps are actually the START of the bar, e.g. 10:00 = 10:00-10:04:59
        print("TIMESTAMPS", df.index[:5]) # REMOVE LATER - returns DatetimeIndex list/RangeIndex...
        print("TIMEZONE", df.index.tz) # REMOVE LATER - UTC confirmed

        df_15m = df.resample("15min").agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum"
        })
        df_15m = df_15m.dropna()

        for _, row in df_15m.iterrows():
            self.bar_window[symbol].append(row)
        
        # latest_macd[symbol] = self.compute_macd(sdf)
        latest_rsi[symbol] = self.compute_rsi(pd.DataFrame(self.bar_window[symbol]))
        print("SEED DATA", self.bar_window[symbol]) # REMOVE LATER
        print("SEED RSI", latest_rsi[symbol]) # REMOVE LATER

        last_bar_time = None
        while True:
            now = datetime.datetime.now(eastern)

            if now.minute % 15 == 0 and now.second < 2 and now > after_first_bar:
                latest_bar_request = StockBarsRequest(
                    symbol_or_symbols=symbol,
                    timeframe=TimeFrame(5, TimeFrame.Minute),
                    limit=1
                )

                bars = historical_client.get_stock_bars(latest_bar_request).df
                print("BARS", bars.head()) # REMOVE LATER
                if bars.empty:
                    await asyncio.sleep(2)
                    continue

                latest_bar_time = bars.index[-1]
                if latest_bar_time != last_bar_time:
                    last_bar_time = latest_bar_time

                    for _, row in bars.iterrows():
                        self.bar_window[symbol].append(row)
                    latest_rsi[symbol] = self.compute_rsi(pd.DataFrame(self.bar_window[symbol]))
                    print("RSI LIST", latest_rsi[symbol]) # REMOVE LATER
            await asyncio.sleep(1)    

    def compute_macd(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        df = df.copy()
        macd = ta.macd(df['close'], fast=12, slow=26, signal=9, append=True)
        return macd
    
    def compute_rsi(self, df: pd.DataFrame) -> float:
        if df.empty:
            return 0
        df = df.copy()
        rsi = ta.rsi(df['close'], length=14)
        return float(rsi.iloc[-1])


# ===== OPEN/CLOSE STREAM, HANDLER CALL UTILS ===== #
handler = DataHandler()

async def start_price_quote_bar_stream(symbols):
    retries = 0
    while True:
        try:
            for symbol in symbols:
                # asyncio.create_task(handler.seed_history_recalc_on_bar(symbol))

                stock_stream.subscribe_trades(handler.handle_trade, symbol)
                # stock_stream.subscribe_quotes(handler.handle_quote, symbol)
                stock_stream.subscribe_bars(handler.handle_bar, symbol)
            
            await stock_stream._run_forever()
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

async def stop_price_quote_bar_stream(symbol):
    try:
        stock_stream.unsubscribe_trades(symbol)
        # stock_stream.unsubscribe_quotes(symbol)
        stock_stream.unsubscribe_bars(symbol)
        print(f"[{symbol}] price/quote stream unsubscribed")
    except Exception as e:
        print (f"[WebSocket] Error unsubscribing from {symbol}: {e}")


# ===== VALUE RETRIEVAL UTILS (to main) ===== #
def get_current_price(symbol):
    return latest_prices.get(symbol)

def get_day_high(symbol):
    return day_high.get(symbol)

def get_latest_macd(symbol):
    df = latest_macd.get(symbol)
    return df.iloc[-1]

def get_latest_rsi(symbol):
    rsi = latest_rsi.get(symbol)
    if rsi is None:
        return 0
    print(symbol, f"RSI: {rsi}") # REMOVE LATER
    return rsi

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
def is_intraday():
    eastern = pytz.timezone("US/Eastern")
    now = datetime.datetime.now(eastern).time()
    return (datetime.time(9,30) <= now < datetime.time(16,0))

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

def close_all_positions():
    try:
        results = trading_client.close_all_positions()    
        eastern = pytz.timezone("US/Eastern")
        now = datetime.datetime.now(eastern)
        for r in results:
            print(f"Closed: {r.symbol} - Qty: {r.qty}")
            with open("trade-log/trade_log.txt", "a") as file:
                file.write(f"{now}, {r.get('symbol')}, {r.get('qty')}, EOD Exit; break even" + "\n")
    except Exception as e:
        print(f"Failed to close positions: {e}")
