import os
import time

import asyncio
import websockets
import threading

import ccxt
from ripio.trade.client import Client

from dotenv import load_dotenv
load_dotenv()

# Initialize Kraken and Ripio clients
kraken = ccxt.kraken({"apiKey": os.getenv("KRAKEN_API_KEY"), "secret": os.getenv("KRAKEN_SECRET_KEY")})
ripio = Client(api_key=os.getenv("RIPIO_API_KEY"))

# Constants
spread = 0.001
order_amount = 100
order_book_lock = threading.Lock()
offset_orders = set()
created_orders = set()
prev_best_bid = None
prev_best_ask = None

def create_ripio_order(pair, side, amount, price):
    global created_orders
    balances = ripio.get_user_balances()

    if side == "buy":
        required_balance = amount * price
        currency_code = "USDC"
    else:  # side == "sell"
        required_balance = amount
        currency_code = "BTC"

    available_balance = 0
    for balance in balances:
        if balance["currency_code"] == currency_code:
            available_balance = float(balance["available_amount"])
            break

    if available_balance >= required_balance:
        params = {
            "pair": pair,
            "side": side,
            "amount": amount,
            "type": "limit",
            "price": price,
        }
        response = ripio.create_order(**params)
        order_id = response["id"]
        created_orders.add(order_id)
    else:
        print(f"Insufficient {currency_code} balance to create {side} order")

async def kraken_ws_ticker():
    global prev_best_bid, prev_best_ask
    async with websockets.connect("wss://ws.kraken.com") as websocket:
        await websocket.send('{"event": "subscribe", "pair": ["BTC/USDC"], "subscription": {"name": "ticker"}}')

        while True:
            message = await websocket.recv()
            message = eval(message)

            if isinstance(message, list) and "ticker" in message:
                data = message[1]

                order_book_lock.acquire()
                best_bid = float(data["b"][0])
                best_ask = float(data["a"][0])

                # Check if the price change is more than half the spread
                if (prev_best_bid is None or abs(prev_best_bid - best_bid) / prev_best_bid > spread / 2) or \
                   (prev_best_ask is None or abs(prev_best_ask - best_ask) / prev_best_ask > spread / 2):

                    # Cancel previous orders
                    for order_id in created_orders:
                        ripio.cancel_order(order_id)
                    created_orders.clear()

                    # Update best bid and ask
                    prev_best_bid = best_bid
                    prev_best_ask = best_ask

                    spread_bid = best_bid * (1 + spread)
                    spread_ask = best_ask * (1 - spread)
                    
                    # Create buy and sell orders on Ripio
                    create_ripio_order("BTC_USDC", "buy", order_amount / spread_bid, spread_bid)
                    create_ripio_order("BTC_USDC", "sell", order_amount / spread_ask, spread_ask)

                order_book_lock.release()

def offset_ripio_order(order_id, side, executed_amount):
    global kraken, ripio

    # Check available balance on Kraken
    kraken_balance = kraken.fetch_balance()
    btc_balance = kraken_balance["total"]["BTC"]
    usdc_balance = kraken_balance["total"]["USDC"]

    # Determine the side of the order to be placed on Kraken
    kraken_side = "sell" if side == "buy" else "buy"

    # Calculate the amount to be offset on Kraken
    if kraken_side == "buy":
        usdc_amount = executed_amount * kraken.fetch_ticker("BTC/USDC")["last"]
        if usdc_amount <= usdc_balance:
            kraken.create_market_order("BTC/USDC", kraken_side, usdc_amount)
            print(f"Offset {kraken_side} order of {usdc_amount} USDC created on Kraken")
        else:
            print("Insufficient USDC balance on Kraken to offset the order")
    else:  # kraken_side == "sell"
        if executed_amount <= btc_balance:
            kraken.create_market_order("BTC/USDC", kraken_side, executed_amount)
            print(f"Offset {kraken_side} order of {executed_amount} BTC created on Kraken")
        else:
            print("Insufficient BTC balance on Kraken to offset the order")


async def monitor_ripio_orders():
    global created_orders
    while True:
        for order_id in created_orders:
            order = ripio.get_order_by_id(order_id)
            if order["status"] in ["executed_completely", "executed_partially"]:
                executed_amount = order["executed_amount"]
                offset_ripio_order(order_id, order["side"], executed_amount)
                print(f"Order {order_id} on Ripio has been offset on Kraken")

                # Remove the executed order from the list
                created_orders.remove(order_id)

                # Create a new order on Ripio with the same side, amount, and price
                new_order_id = create_ripio_order("BTC_USDC", order["side"], order["price"], order["requested_amount"])
                created_orders.append(new_order_id)
                print(f"New order created on Ripio with ID: {new_order_id}")

        await asyncio.sleep(10)


# Run the websocket connection and order monitoring in separate threads
kraken_ws_thread = threading.Thread(target=lambda: asyncio.run(kraken_ws_ticker()))
monitor_ripio_orders_thread = threading.Thread(target=lambda: asyncio.run(monitor_ripio_orders()))

kraken_ws_thread.start()
monitor_ripio_orders_thread.start()

kraken_ws_thread.join()
monitor_ripio_orders_thread.join()
