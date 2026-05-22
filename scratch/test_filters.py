import asyncio
import sys
import os

# Add backend app to path
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'backend'))

from app.config import settings
from app.engine.scanner import PairScanner, SYMBOL_BLACKLIST

async def main():
    scanner = PairScanner()
    print("Initializing AsyncClient...")
    from binance import AsyncClient
    client = await AsyncClient.create(
        api_key=settings.active_api_key,
        api_secret=settings.active_api_secret,
        testnet=settings.is_testnet
    )
    scanner._binance_client = client
    
    print(f"TRADING_MODE: {settings.TRADING_MODE}")
    print(f"is_testnet: {settings.is_testnet}")
    print(f"is_paper: {settings.is_paper}")
    print(f"is_live: {settings.is_live}")
    print(f"SCANNER_MIN_VOLUME_24H: {settings.SCANNER_MIN_VOLUME_24H}")
    print(f"SCANNER_MAX_SPREAD_PCT: {settings.SCANNER_MAX_SPREAD_PCT}")
    print(f"SCANNER_MIN_LISTING_DAYS: {settings.SCANNER_MIN_LISTING_DAYS}")

    print("Fetching exchange info, tickers, and book tickers...")
    exchange_info, tickers_24h, book_tickers = await asyncio.gather(
        client.futures_exchange_info(),
        client.futures_ticker(),
        client.futures_orderbook_ticker(),
    )
    print(f"Fetched {len(exchange_info.get('symbols', []))} symbols, {len(tickers_24h)} tickers, and {len(book_tickers)} book tickers.")

    symbol_info = scanner._parse_exchange_info(exchange_info)
    print(f"Parsed {len(symbol_info)} perpetual USDT symbols.")

    ticker_map = {}
    for t in tickers_24h:
        sym = t.get("symbol", "")
        if sym:
            ticker_map[sym] = t.copy()

    for bt in book_tickers:
        sym = bt.get("symbol", "")
        if sym and sym in ticker_map:
            ticker_map[sym]["askPrice"] = bt.get("askPrice", "0")
            ticker_map[sym]["bidPrice"] = bt.get("bidPrice", "0")

    # Print sample ticker to inspect keys
    if tickers_24h:
        sample_sym = tickers_24h[0].get("symbol", "")
        print("Sample ticker keys after merge:", list(ticker_map[sample_sym].keys()))
        print("Sample ticker values after merge:", ticker_map[sample_sym])

    blacklist_count = 0
    no_ticker_count = 0
    volume_fail_count = 0
    age_fail_count = 0
    spread_fail_count = 0
    passed = []

    for symbol, info in symbol_info.items():
        if symbol in SYMBOL_BLACKLIST:
            blacklist_count += 1
            continue

        ticker = ticker_map.get(symbol)
        if not ticker:
            no_ticker_count += 1
            continue

        # Volume filter
        quote_volume = float(ticker.get("quoteVolume", 0))
        if quote_volume < settings.SCANNER_MIN_VOLUME_24H:
            volume_fail_count += 1
            continue

        # Listing age filter
        if info["listing_age_days"] < settings.SCANNER_MIN_LISTING_DAYS:
            age_fail_count += 1
            continue

        # Spread filter
        ask = float(ticker.get("askPrice", 0))
        bid = float(ticker.get("bidPrice", 0))
        if ask <= 0 or bid <= 0:
            spread_fail_count += 1
            continue

        mid = (ask + bid) / 2
        spread_pct = (ask - bid) / mid if mid > 0 else 1.0
        if spread_pct > settings.SCANNER_MAX_SPREAD_PCT:
            spread_fail_count += 1
            continue

        passed.append((symbol, quote_volume, spread_pct))

    print(f"Blacklist filtered: {blacklist_count}")
    print(f"No ticker info: {no_ticker_count}")
    print(f"Failed volume filter (< {settings.SCANNER_MIN_VOLUME_24H}): {volume_fail_count}")
    print(f"Failed listing age filter (< {settings.SCANNER_MIN_LISTING_DAYS} days): {age_fail_count}")
    print(f"Failed spread filter (> {settings.SCANNER_MAX_SPREAD_PCT}): {spread_fail_count}")
    print(f"Passed hard filters: {len(passed)}")
    if passed:
        print("Top 10 passed by volume:")
        passed.sort(key=lambda x: x[1], reverse=True)
        for p in passed[:10]:
            print(f" - {p[0]}: Volume={p[1]:,}, Spread={p[2]:.5%}")

    await client.close_connection()

if __name__ == '__main__':
    asyncio.run(main())
