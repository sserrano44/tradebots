import json
import ssl

import time
import os

import asyncio
from asyncio.subprocess import Process

import requests
import websockets
from dotenv import load_dotenv

#todo kluge
#HIGHLY INSECURE
ssl_context = ssl.SSLContext()
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE
#HIGHLY INSECURE
#todo kluge

load_dotenv()

CONTEXT = {}
CURRENT_SELL = None
CURRENT_BUY = None

async def listen_orderboook(pair):
    global CONTEXT

    print(f"start listen_orderboook({pair})")

    r = requests.get(f"https://api.ripiotrade.co/v4/book/orders/level-2?pair={pair}", headers={'Authorization': os.environ['API_KEY_V4']})
    if r.status_code != 200:
        raise Exception('wrong code')

    data = r.json()

    CONTEXT[pair] = {
        'buy': data['data']['buying'],
        'sell': data['data']['selling']
    }

    async with websockets.connect("wss://ws.ripiotrade.co", ssl=ssl_context) as ws:
        msg = json.dumps({
            "method": "subscribe",
            "topics": [
                f"orderbook/level_2@{pair}"
        ]})
        # print(msg)
        await ws.send(msg)
        msg = json.loads(await ws.recv()) #ignore subscription
        # print(msg)

        while True:
            msg = json.loads(await ws.recv())
            print(f'got updated {pair}')
            CONTEXT[pair] = msg['body']

async def trader():
    global CONTEXT
    global CURRENT_BUY
    global CURRENT_SELL

    await asyncio.sleep(2)


    def get_balance():
        r = requests.get("https://api.ripiotrade.co/v4/wallets/balance", headers={'Authorization': os.environ['API_KEY_V4']})

        balance = {}
        for currency in r.json()['data']:
            balance[currency['currency_code']] = currency['available_amount']

        return balance

    def calc_price_diff(p1, p2):
        return abs((p1 - p2) / p2)

    while True:
        BUY_PRICE = CONTEXT['USDC_ARS']['buy'][0]['price'] * CONTEXT['BTC_USDC']['buy'][0]['price']
        print(f'BUY PRICE {BUY_PRICE}')

        SELL_PRICE = CONTEXT['USDC_ARS']['sell'][0]['price'] * CONTEXT['BTC_USDC']['sell'][0]['price']
        print(f'SELL PRICE {SELL_PRICE}')

        if CURRENT_BUY and calc_price_diff(CURRENT_BUY['price'],BUY_PRICE) > 0.005:
            print(calc_price_diff(CURRENT_BUY['price'],BUY_PRICE))

            # cancel if need to update
            r = requests.delete("https://api.ripiotrade.co/v4/orders", data={
                'id': CURRENT_BUY['id']
            }, headers={'Authorization': os.environ['API_KEY_V4']})
            print('cancel buy', r.json())
            CURRENT_BUY = None

        if CURRENT_SELL and calc_price_diff(CURRENT_SELL['price'],SELL_PRICE) > 0.005:
            print(calc_price_diff(CURRENT_SELL['price'],SELL_PRICE))

            # cancel if need to update
            r = requests.delete("https://api.ripiotrade.co/v4/orders", data={
                'id': CURRENT_SELL['id']
            }, headers={'Authorization': os.environ['API_KEY_V4']})
            print('cancel sell', r.json())
            CURRENT_SELL = None

        balance = get_balance()

        if not CURRENT_BUY and (balance['ARS'] / BUY_PRICE) > 0.0001:
            r = requests.post("https://api.ripiotrade.co/v4/orders", data={
                "pair": "BTC_ARS",
                "side": "buy",
                "type": "limit",
                "amount": min(0.01, balance['ARS'] / BUY_PRICE),
                "price": BUY_PRICE
            }, headers={'Authorization': os.environ['API_KEY_V4']})

            rdata = r.json()
            if not 'error_code' in rdata:
                print('create buy', rdata)
                CURRENT_BUY = {
                    'price': BUY_PRICE,
                    'id': rdata['data']['id']
                }

        if not CURRENT_SELL and balance['BTC'] > 0.001:
            r = requests.post("https://api.ripiotrade.co/v4/orders", data={
                "pair": "BTC_ARS",
                "side": "sell",
                "type": "limit",
                "amount": min(0.01, balance['BTC']),
                "price": SELL_PRICE
            }, headers={'Authorization': os.environ['API_KEY_V4']})

            rdata = r.json()
            if not 'error_code' in rdata:
                print('create sell', rdata)
                CURRENT_SELL = {
                    'price': SELL_PRICE,
                    'id': rdata['data']['id']
                }

        await asyncio.sleep(5)

async def main():
    t1 = asyncio.create_task(listen_orderboook('USDC_ARS'))
    t2 = asyncio.create_task(listen_orderboook('BTC_USDC'))
    t3 = asyncio.create_task(trader())

    await t1 
    await t2
    await t3

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print('shooting down!')
        if CURRENT_BUY:
            r = requests.delete("https://api.ripiotrade.co/v4/orders", data={
                'id': CURRENT_BUY['id']
            }, headers={'Authorization': os.environ['API_KEY_V4']})

        if CURRENT_SELL:
            # cancel if need to update
            r = requests.delete("https://api.ripiotrade.co/v4/orders", data={
                'id': CURRENT_SELL['id']
            }, headers={'Authorization': os.environ['API_KEY_V4']})

