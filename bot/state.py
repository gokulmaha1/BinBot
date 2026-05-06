def has_open_position(client, symbol):
    try:
        positions = client.futures_position_information(symbol=symbol)
        for p in positions:
            if float(p['positionAmt']) != 0:
                return True
        return False
    except Exception as e:
        print(f"Error checking position: {e}")
        return True # Default to True to prevent double entry on error
