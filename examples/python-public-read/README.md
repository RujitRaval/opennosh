# Python public-read starter

This minimal Python 3.11+ application performs one search, loads the first public food, verifies that
the release proof is bound to that source-qualified record, and prints its attribution.

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python main.py
```

The hosted service is the default. Select a self-hosted origin without editing the code:

```sh
OPENNOSH_TARGET=https://nosh.example OPENNOSH_QUERY=rajma python main.py
```

The client sends no credentials, cookies, automatic retries, or telemetry. A missing or unbound
proof exits unsuccessfully without printing the upstream response body.
