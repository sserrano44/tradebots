## Example scripts for Trading on Ripio Trade (https://trade.ripio.com)

### Step 0 - recommended 
Install a python enviroment manager like miniconda (https://docs.conda.io/en/latest/miniconda.html)
Once installed make a dedicated enviroment
``
$ conda create -n tradebots python=3.9
$ conda activate tradebots 
``

### Step 1 - Clone our this repo and clone ccxt

``
$ git clone git@github.com:ripio/ccxt.git
$ git clone git@github.com:sserrano44/tradebots.git
``

### Step 2 - Install ccxt

``
$ pip install multidict==4.5 # might not be required to force install multidict (but I had to do it)
$ cd ccxt/python
$ python setup.py install
$ cd ../..
``
### Step 3 - Install tradebots requirements

``
$ cd tradebots
$ pip install -r requirements.txt
``

### Step 4 - get API KEY

``
# https://trade.ripio.com/market/api/token
# write to .env file
$ echo 'API_KEY=REPLACE_WITH_API_KEY' > .env
``

## Example RUN for buying 5000 usdc at 330 pesos in 50 usdc chunks

``
$ conda activate tradebots
$ python buy_usdc.py 5000 330 50
``