"""Read-only Streamlit dashboard for the trading bot.

The package is importable without streamlit: `config`, `db_reader` and
`charts` are plain Python (stdlib / pandas / plotly), and only `app` and
`components` touch the web framework. That split is what lets the test suite
exercise the queries and the metric math without a browser.
"""
