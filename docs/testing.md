# Testing

```bash
python -m pytest                 # offline tests: database, parsers, API, security
python -m pytest -m live         # live tests against the real sources (network)

node --test tests/api_client.test.cjs       # the client's request helper (Node.js)
node --test tests/source_health.test.cjs    # sidebar source health rules (Node.js)
node --test tests/source_transfer.test.cjs  # source transfer UI logic (Node.js)
```

The Python suite needs the dev dependencies (`uv sync` installs them). With pip,
run `pip install pytest pytest-asyncio` first.
