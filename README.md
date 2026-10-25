# ba-livetraffic

Bratislava live traffic tracker using the TomTom Traffic API.
Collects data every 30 minutes via GitHub Actions into `data/`.

    pip install -r requirements.txt
    python traffic.py collect      # key is read from .env (not committed)
    python traffic.py stats

Repo: https://github.com/lalasonycierny/ba-livetraffic
