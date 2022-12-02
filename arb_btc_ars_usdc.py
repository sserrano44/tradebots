
import time
import os
import json
import ssl
import logging

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

CONTEXT = {
    'orders': []
}
CURRENT_SELL = None
CURRENT_BUY = None
RUNNING = True


def get_balance():
    r = requests.get("https://api.ripiotrade.co/v4/wallets/balance", headers={'Authorization': os.environ['API_KEY_V4']})

    balance = {}
    for currency in r.json()['data']:
        balance[currency['currency_code']] = currency['available_amount']

    return balance

def rebalance_buy(order_data):
    """ Rebalance BTC/ARS buy order by doing two trades one sell BTC/USDC and another USDC/ARS sell order """
    balance = get_balance()
    btc_amount = order_data['executed_amount']
    btc_usdc_sell = {
        'pair': 'BTC_USDC',
        'side': 'sell',
        'amount': min(btc_amount, balance['BTC']),
        'price': 0,
        'type': 'market'
    }
    r = requests.post("https://api.ripiotrade.co/v4/orders", headers={'Authorization': os.environ['API_KEY_V4']}, json=btc_usdc_sell)

    if r.status_code != 200:
        logging.error(f"Error placing BTC_USDC sell order: {r.text}")
        return

    r = requests.get(f"https://api.ripiotrade.co/v4/orders/{r.json()['data']['id']}", headers={'Authorization': os.environ['API_KEY_V4']})
    if r.status_code != 200:
        logging.error(f"Error getting BTC_USDC sell order: {r.text}")
        return

    usdc_amount = r.json()['data']['total_value']
    # sell usdc for ars
    usdc_ars_sell = {
        'pair': 'USDC_ARS',
        'side': 'sell',
        'amount': usdc_amount,
        'price': 0,
        'type': 'market'
    }

    r = requests.post("https://api.ripiotrade.co/v4/orders", headers={'Authorization': os.environ['API_KEY_V4']}, json=usdc_ars_sell)
    if r.status_code != 200:
        logging.error(f"Error placing USDC_ARS sell order: {r.text}")
        return

    return

def rebalance_sell(order_data):
    """ rebalance BTC/ARS sell order by doing two trades one buy USDC/ARS and another BTC/USDC buy order """
    balance = get_balance()

    ars_amount = order_data['total_value']
    usd_ars_price = CONTEXT['USDC_ARS']['sell'][0]['price']
    usdc_amount = min(ars_amount, balance['ARS']) / usd_ars_price

    usdc_ars_buy = {
        'pair': 'USDC_ARS',
        'side': 'buy',
        'amount': usdc_amount,
        'price': 0,
        'type': 'market'
    }

    r = requests.post("https://api.ripiotrade.co/v4/orders", headers={'Authorization': os.environ['API_KEY_V4']}, json=usdc_ars_buy)
    if r.status_code != 200:
        logging.error(f"Error placing USDC_ARS buy order: {r.text}")
        return

    r = requests.get(f"https://api.ripiotrade.co/v4/orders/{r.json()['data']['id']}", headers={'Authorization': os.environ['API_KEY_V4']})
    
    if r.status_code != 200:
        logging.error(f"Error getting USDC_ARS buy order: {r.text}")
        return
    
    usdc_amount = r.json()['data']['executed_amount']
    btc_usdc_price = CONTEXT['BTC_USDC']['sell'][0]['price']
    btc_amount = usdc_amount / btc_usdc_price

    btc_usdc_buy = {
        'pair': 'BTC_USDC',
        'side': 'buy',
        'amount': btc_amount,
        'price': 0,
        'type': 'market'
    }
    r = requests.post("https://api.ripiotrade.co/v4/orders", headers={'Authorization': os.environ['API_KEY_V4']}, json=btc_usdc_buy)
    if r.status_code != 200:
        logging.error(f"Error placing BTC_USDC buy order: {r.text}")
        return
    
    return

async def listen_orderboook(pair):
    global CONTEXT, RUNNING

    print(f"start listen_orderboook({pair})")

    r = requests.get(f"https://api.ripiotrade.co/v4/book/orders/level-2?pair={pair}", headers={'Authorization': os.environ['API_KEY_V4']})
    if r.status_code != 200:
        raise Exception('wrong code')

    data = r.json()

    CONTEXT[pair] = {
        'buy': data['data']['buying'],
        'sell': data['data']['selling']
    }

    while RUNNING:
        try:
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

                while RUNNING:
                    msg = json.loads(await ws.recv())
                    print(f'got updated {pair}')
                    CONTEXT[pair] = msg['body']
        except asyncio.exceptions.CancelledError:
            return
        except:
            # log all exceptions
            logging.exception("webscoket error")
            await asyncio.sleep(5)

async def trader():
    global CONTEXT, CURRENT_BUY, CURRENT_SELL

    await asyncio.sleep(2)

    def calc_price_diff(p1, p2):
        return abs((p1 - p2) / p2)

    while RUNNING:

        # check ticker connection
        try:
            r = requests.get("https://api.ripiotrade.co/v4/public/tickers/BTC_ARS", headers={'Authorization': os.environ['API_KEY_V4']})
            if r.status_code != 200:
                print('wrong code')
                await asyncio.sleep(5)
                continue
        except:
            logging.exception("get ticker error")
            await asyncio.sleep(5)
            continue

        BUY_PRICE = CONTEXT['USDC_ARS']['buy'][0]['price'] * CONTEXT['BTC_USDC']['buy'][0]['price']
        print(f'BUY PRICE {BUY_PRICE}')

        SELL_PRICE = CONTEXT['USDC_ARS']['sell'][0]['price'] * CONTEXT['BTC_USDC']['sell'][0]['price']
        print(f'SELL PRICE {SELL_PRICE}')

        if CURRENT_BUY:
            r = requests.get("https://api.ripiotrade.co/v4/orders", data={
                'id': CURRENT_BUY['id']
            }, headers={'Authorization': os.environ['API_KEY_V4']})        

            if r.status_code == 200 and r.json()['data']['executed_amount'] > 0:
                rebalance_buy(r.json()['data'])
                CURRENT_BUY = None
            elif calc_price_diff(CURRENT_BUY['price'],BUY_PRICE) > 0.005:
                # cancel if need to update
                r = requests.delete("https://api.ripiotrade.co/v4/orders", data={
                    'id': CURRENT_BUY['id']
                }, headers={'Authorization': os.environ['API_KEY_V4']})
                print('cancel buy', r.json())
                CURRENT_BUY = None

        if CURRENT_SELL:
            r = requests.get("https://api.ripiotrade.co/v4/orders", data={
                'id': CURRENT_SELL['id']
            }, headers={'Authorization': os.environ['API_KEY_V4']})

            if r.status_code == 200 and r.json()['data']['executed_amount'] > 0:
                rebalance_sell(r.json()['data'])
                CURRENT_SELL = None
            elif calc_price_diff(CURRENT_SELL['price'],SELL_PRICE) > 0.005:
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
                CONTEXT['orders'].append(rdata['data']['id'])

        if not CURRENT_SELL and balance['BTC'] > 0.0001:
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
                CONTEXT['orders'].append(rdata['data']['id'])

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
        RUNNING = False

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

