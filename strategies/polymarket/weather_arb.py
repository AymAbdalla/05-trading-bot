"""Weather Arb: trade the airport station, not the phone app.

THE CLAIM. Polymarket temperature markets resolve on an OFFICIAL weather
station named in the market's own rules text - typically an airport ASOS/AWOS
site. Retail participants anchor on whatever their phone shows, which is a
downtown or neighbourhood grid cell from a consumer forecast API. Airport and
downtown are two different measurements, and a social media post claims the gap
runs 3 to 8 degrees Fahrenheit.

## THAT NUMBER IS NOT EVIDENCE, AND NOTHING HERE TREATS IT AS ONE

The 3-8F figure is a claim from a social media post. It is a HYPOTHESIS. It is
not our data, we have never measured it, and no position of this strategy has
ever resolved. Every decision row this strategy emits - entries and skips alike
- carries `claimed_gap_is_unverified_vendor_number=True` and
`gap_never_measured_by_us=True`, exactly as `fair_value_arb` stamps its Reddit
provenance, so no later reader can pick "3 to 8 degrees" off a log and mistake
it for a measurement.

The gap is also NOT SIGNED. Urban heat island runs one way overnight (downtown
holds heat, the airport on its open field cools faster) and a sea breeze or lake
breeze runs the other way in the afternoon at a coastal airport. Anyone who
hardcodes "the airport is colder" has hardcoded half a diurnal cycle. Nothing
below assumes a direction: the airport reading is used as the
resolution-relevant observation, and the downtown reading is fetched only to
MEASURE the gap and its sign, never to gate a trade.

## WHAT A REAL POLYMARKET TEMPERATURE MARKET ACTUALLY LOOKS LIKE

Measured live on 2026-08-18 off `GET /events?tag_slug=weather&closed=false`:
2,045 markets under the tag, 1,771 of them city temperature markets, listed as
a LADDER per city per day. Eleven mutually exclusive rungs: two tails and nine
interior buckets.

    Will the highest temperature in Tokyo be 25C or below on August 18?
    Will the highest temperature in Tokyo be 26C on August 18?          <- bucket
    ...
    Will the highest temperature in Tokyo be 35C or higher on August 18?

    Will the highest temperature in New York City be 75F or below ...?
    Will the highest temperature in New York City be between 76-77F ...? <- bucket
    ...

Three facts in there each broke the original parser, and each is a way to lose
the whole position rather than a little of it:

  1. THE COMPARISON WORD COMES AFTER THE NUMBER. "85F or below", not
     "below 85F". The original `_THRESHOLD_RE` required it before, so it
     returned None on 100% of live questions and this strategy could not fire
     on a single real market.
  2. MOST OF THESE MARKETS ARE CELSIUS. 1,485 of 1,771 measured. `value_f` is
     Fahrenheit and is compared against a Fahrenheit METAR reading. Parsing
     "30C" into `value_f=30.0` prices 30F against an 86F market, a 56F error,
     and would look like a screaming edge on every single rung. The unit is
     parsed explicitly and a question with NO readable unit is refused rather
     than assumed - there is no default unit anywhere in this file.
  3. A RUNG IS AN INTERVAL, NOT A COMPARISON. "be 84F" means the reported daily
     high equals 84, and the rules text states the source reports whole
     degrees, so as a continuous interval that rung is [83.5, 84.5). Pricing it
     as "above 84" prices a tail at roughly twice the true probability.

Because the interval is built in the market's NATIVE unit and converted
afterwards, a Celsius bucket is 1.8F wide, not 1.0F. Doing the half-step in
Fahrenheit first would make every Celsius rung 44% too narrow. `parse_threshold`
does the arithmetic in the native unit and converts both edges, and
`test_a_celsius_bucket_is_widened_before_conversion_not_after` pins the
ordering.

There is a SECOND market family under the same tag that must never be priced
here: "Will global temperature increase by more than 1.29C in August 2026?".
It is a planetary anomaly index, not a city airport station, and it carries the
MASCULINE ORDINAL character U+00BA rather than the degree sign. It gets its own
exclusion and its own skip reason (`global_temperature_market_excluded`) so it
can never be pooled with a genuine parse failure.

## THE MODEL PRICES THE WRONG VARIABLE, AND THE ROWS SAY SO

Every one of these markets resolves on the DAILY EXTREME - the highest (or
lowest) reading of the calendar day at the station. `probability_yes` prices
the distribution of a SINGLE FUTURE READING at the resolution timestamp. Those
are different random variables, and the daily max of a path is stochastically
larger than the path's endpoint, so this model is BIASED LOW on every "or
higher" rung and BIASED HIGH on every "or below" rung of a "highest
temperature" ladder, and the other way on a "lowest temperature" ladder.

THIS IS NOT A CAVEAT, IT IS A REFUSAL. Measured 2026-08-18 over 80 live
markets with real books and real METAR, the fixed parser produced 7 entries
with realised "edge" between 0.45 and 0.999. Two of them, same minute:

    Madrid, station 33.0C. Market: "the highest today is 39C" at 0.70,
    because the afternoon peak has not happened. Model: 0.000024.
    Buenos Aires, station 7.0C. Market: "highest today is 8C or below" at
    0.001, because the afternoon will be warmer. Model: 0.87.

The market is right both times and the model is wrong in OPPOSITE directions,
which is the worst case: a pooled win rate would average the two biases into
something that looks unbiased. So `allow_daily_extreme_markets` defaults to
False and every daily-extreme market is refused under
`daily_extreme_not_priced_by_point_in_time_model` - a convention 11 cannot-run,
not a claim there is no edge there. The flag exists for gathering tape.

The consequence, stated plainly: THE PARSER IS FIXED AND THE STRATEGY STILL
BOOKS ZERO LIVE ENTRIES, because 100% of the live universe is daily-extreme.
That is the honest state. An 86-cent edge against a market quoting 0.1c is the
`COST_FLOOR = -0.30` shape (convention 17), and shipping it as a fill would put
fabricated wins in a paper log that somebody scores later.

Every row carries `market_metric` and
`model_prices_point_in_time_not_daily_extreme`. Fixing it for real needs a
running daily max plus a diurnal climatology, and that is not built.

## THE RESOLUTION SOURCE IS THE RULES TEXT, NEVER OUR ASSUMPTION

`WEATHER_MARKETS` below carries an ICAO station per city. Those are ASSUMPTIONS.
They are used to decide which station to FETCH and to cross-check, and they are
never used to decide what a market resolves on. `resolution_station_checked`
reads the market's own rules text and refuses the market when it cannot find a
station there:

    resolution_station_unknown     no rules text, or no station we recognise
    resolution_station_ambiguous   the rules text names two different stations

Both are SKIPs, and both are convention 11 cannot-runs rather than results.
Guessing a market's resolution source is how you lose the entire position while
being completely right about the weather. If the rules name a station that is
not in `STATION_ALIASES`, that reads as `resolution_station_unknown`, which is
the correct answer: we could not read it, so we do not trade it.

When the rules text and the table disagree, the RULES WIN and the row is
stamped `station_assumption_matches_rules=False`. That flag existing at all is
the point: it is the only way a wrong table entry becomes visible instead of
becoming a silent loss.

## OUR PROBABILITY MODEL, AND WHY IT IS THE WEAKEST PART OF THIS FILE

Given an airport reading `T` now and `h` hours until resolution, we price
P(final reading beats the threshold) as a normal CDF with

    sigma_F = SIGMA_FLOOR_F + SIGMA_PER_SQRT_HOUR_F * sqrt(h)

That is a diffusion assumption applied to a variable that is not a diffusion.
Temperature has a large, deterministic, strongly mean-reverting daily cycle: at
06:00 the next six hours are almost certainly UP and at 16:00 they are almost
certainly DOWN, and a symmetric random walk centred on the current reading says
neither. So this model is wrong in a KNOWN direction at KNOWN times of day, and
the constants are house numbers with an expiry date (convention 17). Replacing
them with a diurnal-climatology model is the obvious next step and is not built.
`MIN_EDGE = 0.08` is deliberately wide partly to absorb this.

## WHAT THIS STRATEGY IS NOT_TESTED ON (convention 11)

  - We have never resolved a weather position. Not one. Zero settled trades.
  - The airport-vs-downtown gap has never been measured against our own data.
    Nothing in this repo has ever recorded a paired (airport, downtown) reading.
  - The sigma model above has never been scored against realised intraday
    temperature paths.
  - The daily-extreme-versus-endpoint bias described above has never been
    quantified. It is stated, not measured.
  - `AirportWeatherFeed` reports the CURRENT reading. Nothing in this file
    tracks the running daily max, which is half the resolution variable on a
    "highest temperature" market and is already in the past by the afternoon.

What would measure the gap: a recorder that polls `AirportWeatherFeed` and
`DowntownWeatherFeed` for every city in `WEATHER_MARKETS` on a fixed cadence and
writes both readings plus their difference to a table, the same shape as
`engine/feeds/liquidation_recorder.py`. After a few weeks of tape, the honest
distribution of the gap by city and by hour of day is a query, not a claim. That
recorder does not exist. Until it does, "3 to 8 degrees" is a rumour this file
is careful never to launder.

## FETCHES INSIDE evaluate(), AND WHY THAT IS A COMPROMISE

`base.MarketContext` is a plain data bag precisely so a decision is reproducible
from a logged context. This strategy bends that: `MarketContext` carries no
weather fields, so `evaluate` calls its injected feeds. Both feeds are
constructor arguments (`airport_feed=`, `downtown_feed=`), both are fully
offline-injectable, and every reading that entered a decision is written into
`features` so the row is at least reconstructible after the fact. Rows are
stamped `readings_fetched_inside_evaluate=True`. A caller that wants strict
reproducibility should pass pre-fetched feed stubs.

## HOLDS TO RESOLUTION

`manages_exits = False`. There is no exit model here and no attempt at one: the
thesis is about where the thermometer sits at settlement, so selling early is a
different strategy. The stop is 0.00 - a losing binary share is worth exactly
zero, which satisfies convention 8.

KILL CONDITION: trailing-20 win rate below 55%, measured by
`backtest/polymarket_harness.py` on the `PM_weather_arb` population once 20
resolved trades exist. 55% is not arbitrary: entries are gated at an 8c edge, so
at a typical 0.40-0.60 entry premium the break-even win rate is the entry
premium itself, and 55% is roughly break-even-plus-nothing for a 50c fill. Below
it the 8c "edge" is not edge, it is model error. The harness must score this
population on RESOLVED trades only; there are no closed-before-resolution trades
here to pool with (contrast `fair_value_arb`, which has both). Convention 7 cuts
both ways here and 20 trades is a thin sample either way: a PASS on 20 is a
shrug too, and this line is a stop-loss on the hypothesis, not a promotion test.

SECOND KILL CONDITION, on the thesis rather than on the PnL: if the recorder
described above ever measures a median |airport - downtown| gap below 1.0F for a
city, that city is removed from `WEATHER_MARKETS`. A 3-8F claim that measures
under 1F is not a small edge, it is a wrong premise.

THIRD KILL CONDITION, on the model rather than on the thesis, and this is the
binding one today: `allow_daily_extreme_markets` may only be turned on once
`probability_yes` prices the DAILY EXTREME, and the named harness for that is
`backtest/measure_daily_extreme_calibration.py`, WHICH DOES NOT EXIST. The
number it has to clear: on at least 200 resolved daily-extreme rungs, mean
|model_p - realised_frequency| below 0.05 in each of ten probability deciles.
Until that harness exists and clears that number, an entry on a daily-extreme
market is a fabricated fill. Naming a harness that has not been written is
deliberate - convention 6 wants a number and a named harness, and an honest
"not built" is the difference between a kill condition and a hope.
"""
import math
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import NormalDist
from typing import Dict, List, Optional, Tuple

from strategies.polymarket.base import (Decision, Leg, MarketContext,
                                        PolymarketStrategy, effective_ask_for)

# Never False in this repo. Nothing here has live-trading authority.
PAPER_MODE = True

# ---------------------------------------------------------------------------
# Endpoints. GET only, both of them, no auth, no key, no wallet.
# ---------------------------------------------------------------------------

AVIATION_WEATHER_URL = 'https://aviationweather.gov/api/data/metar'
OPEN_METEO_URL = 'https://api.open-meteo.com/v1/forecast'

#: Both feeds sit on the critical path of a decision, so they get a SHORT
#: timeout. A 10s hang on a weather API is a decision that arrives after the
#: book has moved, which is worse than no decision at all.
FEED_TIMEOUT_SEC = 2.0
FEED_RETRIES = 2
FEED_BACKOFF_SEC = 0.25

USER_AGENT = '05-trading-bot/paper (read-only)'

# ---------------------------------------------------------------------------
# Thresholds. Every one of these is OURS and every one is an assumption with an
# expiry date (convention 17).
# ---------------------------------------------------------------------------

#: A METAR observation older than this is refused. One hour is a long time
#: during a frontal passage: a cold front can drop a station 15F in twenty
#: minutes, and a 59-minute-old reading taken during one is simply a different
#: temperature. This is the gate that stops us pricing yesterday's air.
MAX_OBS_AGE_SEC = 3600

#: Minimum gap between our own probability and the walked entry price. 8c is
#: wide on purpose: it has to absorb the sigma model's known diurnal bias (see
#: the module docstring) as well as the spread. EXPIRY: tighten only after the
#: harness scores a win rate at 8c, never because 8c produced too few trades.
MIN_EDGE = 0.08

#: Uncertainty in the final reading, in degrees F, as a function of hours
#: remaining. HOUSE NUMBERS, never fitted, never backtested. See the module
#: docstring for why the functional form itself is suspect.
SIGMA_FLOOR_F = 0.75
SIGMA_PER_SQRT_HOUR_F = 1.5

#: Past this the sqrt term stops meaning anything and the diurnal cycle owns the
#: answer completely. Beyond it we refuse rather than extrapolate a model we
#: already know is wrong.
MAX_HOURS_TO_RESOLUTION = 36.0

#: Target size, per-trade notional cap and exchange minimum. The notional cap
#: matches `PolymarketPaperAdapter.notional_cap_usdc` and
#: `PolymarketRiskGate.DEFAULT_NOTIONAL_CAP_USDC`; restated here so a size
#: computed in this file cannot silently exceed a cap enforced somewhere else.
TARGET_SHARES = 20
MAX_NOTIONAL_USDC = 10.0
MIN_SHARES = 5

PRICE_TICK = 0.01

#: Celsius to Fahrenheit, defined in one place.
C_TO_F_SCALE = 9.0 / 5.0
C_TO_F_OFFSET = 32.0

SECONDS_PER_HOUR = 3600.0

# ---------------------------------------------------------------------------
# The station table. READ THE WARNING.
# ---------------------------------------------------------------------------

#: city key -> station and geometry ASSUMPTIONS.
#:
#: `icao` here is what we FETCH and cross-check. It is NOT what a market
#: resolves on. Polymarket has used more than one station for the same city over
#: time (NYC contracts have referenced both the Central Park co-op station and
#: LaGuardia), and nothing stops them changing it in the next contract. EVERY
#: ONE of these assignments must be checked against the specific market's rules
#: text before any live use, which is what `resolution_station_checked` does and
#: why an unreadable rules text is a refusal rather than a fallback to this
#: table.
#:
#: `aliases` are the strings a rules text is likely to use. Their mapping onto
#: an ICAO lives in `STATION_ALIASES`, which is the authoritative direction.
#:
#: `downtown` lat/lon is the CITY CENTRE, which is the consumer-app anchor we
#: are claiming retail reads. Approximate to about a city block, which is far
#: finer than open-meteo's grid resolution anyway.
WEATHER_MARKETS: Dict[str, dict] = {
    'nyc': {
        'slug_patterns': ('nyc-temp', 'new-york-temp',
                          'highest-temperature-in-nyc'),
        'icao': 'KNYC',
        'aliases': ('KNYC', 'KLGA', 'Central Park', 'LaGuardia', 'La Guardia'),
        'downtown': (40.7128, -74.0060),
    },
    'la': {
        'slug_patterns': ('la-temp', 'los-angeles-temp',
                          'highest-temperature-in-la'),
        'icao': 'KLAX',
        'aliases': ('KLAX', 'Los Angeles International'),
        'downtown': (34.0522, -118.2437),
    },
    'chicago': {
        'slug_patterns': ('chicago-temp', 'highest-temperature-in-chicago'),
        'icao': 'KMDW',
        'aliases': ('KMDW', 'KORD', 'Midway', "O'Hare", 'OHare'),
        'downtown': (41.8781, -87.6298),
    },
    'miami': {
        'slug_patterns': ('miami-temp', 'highest-temperature-in-miami'),
        'icao': 'KMIA',
        'aliases': ('KMIA', 'Miami International'),
        'downtown': (25.7617, -80.1918),
    },
    'denver': {
        'slug_patterns': ('denver-temp', 'highest-temperature-in-denver'),
        'icao': 'KDEN',
        'aliases': ('KDEN', 'Denver International'),
        'downtown': (39.7392, -104.9903),
    },
}

#: Alias (lowercased) -> the ICAO it means. EXPLICIT, because inferring
#: "LaGuardia means KLGA" from list position is the same class of mistake as
#: inferring a resolution source from a slug. This is the only mapping
#: `resolution_station_checked` consults, and a station absent from it is
#: unreadable rather than assumed.
STATION_ALIASES: Dict[str, str] = {
    'knyc': 'KNYC',
    'klga': 'KLGA',
    'central park': 'KNYC',
    'laguardia': 'KLGA',
    'la guardia': 'KLGA',
    'klax': 'KLAX',
    'los angeles international': 'KLAX',
    'kmdw': 'KMDW',
    'kord': 'KORD',
    'midway': 'KMDW',
    "o'hare": 'KORD',
    'ohare': 'KORD',
    'kmia': 'KMIA',
    'miami international': 'KMIA',
    'kden': 'KDEN',
    'denver international': 'KDEN',
}

#: Cities whose markets carry NO station identifier anywhere in their rules
#: text, mapped to the station we believe they mean. MEASURED, then checked:
#: of the four cities with an empty `resolutionSource` (Hong Kong, Istanbul,
#: Moscow, Tel Aviv), THREE turn out to name their station inside a URL in the
#: `description` field (`weather.gov/wrh/timeseries?site=LTFM` and friends), so
#: `stations_in_urls` reads them straight off the rules text and this table is
#: never consulted for them. They are listed anyway because a silent
#: disagreement between what we measured and what the code reads is exactly the
#: failure this file exists to prevent.
#:
#: Note MOSCOW is UUWW (Vnukovo), NOT UUEE (Sheremetyevo). The rules text says
#: `site=UUWW`. Those are two airports 60km apart.
#:
#: HONG KONG IS THE ONE REAL FALLBACK AND IT IS A PROXY, NOT A MATCH. Its
#: markets resolve on the Hong Kong Observatory headquarters reading published
#: at weather.gov.hk, an URBAN station in Tsim Sha Tsui. VHHH is Chek Lap Kok,
#: an island airport 25km west. Trading VHHH as if it were HKO is precisely the
#: airport-versus-downtown gap this strategy claims to exploit, pointed at our
#: own head. So the table is OFF by default (`allow_station_fallback=False`)
#: and a row that used it is stamped `station_is_a_fallback_guess=True`.
CITY_STATION_FALLBACK: Dict[str, str] = {
    'hong kong': 'VHHH',
    'istanbul': 'LTFM',
    'moscow': 'UUWW',
    'tel aviv': 'LLBG',
}

#: Fields on a Gamma market's raw payload that can carry the rules text. Gamma
#: is not consistent about which one is populated, so all of them are read and
#: joined rather than one being trusted.
RULES_TEXT_FIELDS = ('description', 'rules', 'resolutionSource',
                     'resolution_source', 'resolutionCriteria')

#: Discovery goes through the TAG route, not `/public-search`. Measured on
#: 2026-08-18: `/public-search` returned 33 markets, every one of which mapped
#: to `by_city={'unmapped': 33}`; `tag_slug=weather` returned 2,045 markets
#: across 227 events, 1,771 of them city temperature ladders. A search endpoint
#: that silently returns 1.6% of the universe is worse than one that fails,
#: because the shortfall reads as "Polymarket listed nothing today".
WEATHER_TAG_SLUGS = ('weather', 'temperature')
GAMMA_EVENTS_PATH = '/events'

#: Gamma caps a page at 100. `GAMMA_MAX_PAGES` is a runaway guard, not a limit
#: we expect to hit: 227 events is 3 pages. If discovery ever reports
#: `pagination_capped=True`, the universe grew and this number is stale
#: (convention 17), and the correct response is to raise it, never to trust the
#: truncated count.
GAMMA_PAGE_LIMIT = 100
GAMMA_MAX_PAGES = 40

#: A market has to look like a temperature market to survive discovery. Matched
#: against the question text, lowercased. NOT sufficient on its own - see
#: `looks_like_a_temperature_market`, because a real Polymarket question reads
#: "Will NYC exceed 85F on August 18?" and contains none of these words.
TEMPERATURE_KEYWORDS = ('temperature', 'degrees', 'fahrenheit', 'hottest',
                        'coldest')

#: Every SKIP reason `evaluate` can produce. Listed so a reader can see at a
#: glance that no two causes share a string (convention 20), and so a test can
#: assert every one of them is reachable rather than trusting the docstring
#: (convention 22).
SKIP_REASONS = (
    'no_market',
    'no_clock',
    'global_temperature_market_excluded',
    'resolution_station_unknown',
    'resolution_station_ambiguous',
    'threshold_unparseable',
    'source_reporting_precision_unknown',
    'source_precision_finer_than_ladder_step',
    'daily_extreme_not_priced_by_point_in_time_model',
    'resolution_time_unknown',
    'market_past_resolution_time',
    'resolution_too_far_out',
    'airport_reading_unavailable',
    'airport_obs_time_missing',
    'airport_obs_stale',
    'no_orderbook',
    'no_asks',
    'market_implied_direction_unreadable',
    'airport_agrees_with_market',
    'unsizable_at_notional_cap',
    'unfillable_at_cap',
    'effective_ask_above_cap',
    'edge_below_min',
)

_NORMAL = NormalDist(0.0, 1.0)


def c_to_f(temp_c: float) -> float:
    """Celsius to Fahrenheit. One definition, used by both feeds."""
    return float(temp_c) * C_TO_F_SCALE + C_TO_F_OFFSET


def f_to_c(temp_f: float) -> float:
    """The inverse, so a row can report the METAR's own units."""
    return (float(temp_f) - C_TO_F_OFFSET) / C_TO_F_SCALE


def floor_to_tick(price: float, tick: float = PRICE_TICK) -> float:
    """Snap a limit DOWN onto the 1c grid.

    The epsilon is load-bearing for the same reason it is in
    `fair_value_arb.floor_to_tick`: `0.29 / 0.01` is 28.999999999999996 in
    binary floating point, so a bare floor moves a price already on the grid
    down a full tick.
    """
    if tick <= 0:
        return price
    steps = math.floor(price / tick + 1e-9)
    decimals = max(0, -math.floor(math.log10(tick)))
    return round(steps * tick, decimals)


def _parse_iso_seconds(value) -> Optional[int]:
    """ISO 8601 -> unix seconds, or None. Naive strings are read as UTC.

    Reading a naive timestamp as UTC is an assumption, and it is the right one
    for both feeds here (open-meteo returns UTC unless asked otherwise), but it
    is exactly the kind of assumption that silently shifts an age check by hours
    if a feed ever changes. Ages computed from it are reported on the row, never
    hidden. An int or float epoch passes through unchanged, because Gamma
    sometimes sends one where it usually sends a string.
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return None if not math.isfinite(float(value)) else int(value)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace('Z', '+00:00')
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


# ---------------------------------------------------------------------------
# Readings and feeds
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Reading:
    """One temperature observation, always in Fahrenheit.

    `observed_ts` is None when the source did not tell us when it was taken.
    That is NOT the same as "just now" and is never treated as such: an
    observation we cannot age is refused, because a stale reading and a fresh
    one are the same number with completely different meanings.
    """

    source: str              # 'airport_metar' | 'downtown_open_meteo'
    station: str             # ICAO, or 'lat,lon' for the downtown grid cell
    temp_f: float
    observed_ts: Optional[int] = None
    raw: Optional[str] = None

    def age_sec(self, now: float) -> Optional[float]:
        if self.observed_ts is None:
            return None
        return float(now) - float(self.observed_ts)

    def to_dict(self) -> dict:
        return {'source': self.source, 'station': self.station,
                'temp_f': round(self.temp_f, 2),
                'observed_ts': self.observed_ts}


class _HttpFeed(object):
    """Shared GET, bounded retry and never-raise behaviour for both feeds.

    `session` and `sleep_fn` are injectable so tests run fully offline and
    without real sleeps. Nothing here raises: a failed read returns
    `(None, reason)` and the caller skips. Returning a temperature we could not
    verify would be worse than returning nothing, and returning `None` with no
    reason would pool an outage with a genuinely absent field (convention 20).
    """

    url = ''
    source_name = ''

    def __init__(self, session=None, timeout: float = FEED_TIMEOUT_SEC,
                 retries: int = FEED_RETRIES, sleep_fn=None):
        if session is None:
            # Imported lazily. A strategy module should stay importable without
            # a network stack, the same reason `base.effective_ask_for` defers
            # its engine import.
            import requests
            session = requests.Session()
            session.headers.update({'User-Agent': USER_AGENT})
        self.session = session
        self.timeout = float(timeout)
        # retries=0 would mean "never send the request", which is never what a
        # caller means by it.
        self.retries = max(1, int(retries))
        self._sleep = sleep_fn or time.sleep
        #: Counted by cause. A run that failed on the network and a run that got
        #: clean 404s need different responses, and one combined number cannot
        #: tell them apart.
        self.stats: Dict[str, int] = {}

    def _bump(self, key: str) -> None:
        self.stats[key] = self.stats.get(key, 0) + 1

    def _get_json(self, params: dict) -> Tuple[Optional[object], str]:
        """GET and decode. `(payload, 'ok')` or `(None, reason)`.

        Retries network errors, 429 and 5xx. Does NOT retry other 4xx: a 400 on
        a bad ICAO is a real answer from the server, and retrying it is extra
        load for the same answer.
        """
        for attempt in range(self.retries):
            is_last = attempt == self.retries - 1
            self._bump('requests')
            try:
                resp = self.session.get(self.url, params=params,
                                        timeout=self.timeout)
            except Exception:                            # noqa: BLE001
                # Deliberately broad. A feed exception must never escape into
                # `evaluate`, whatever the injected session decided to raise.
                if is_last:
                    self._bump('fail_network')
                    return None, 'feed_network_failure'
                self._bump('retries')
                self._sleep(FEED_BACKOFF_SEC * (2 ** attempt))
                continue

            code = getattr(resp, 'status_code', None)
            if code == 200:
                try:
                    return resp.json(), 'ok'
                except Exception:                        # noqa: BLE001
                    self._bump('fail_bad_json')
                    return None, 'feed_bad_json'

            if code == 429 or (isinstance(code, int) and code >= 500):
                if is_last:
                    self._bump('fail_http_transient')
                    return None, 'feed_http_transient'
                self._bump('retries')
                self._sleep(FEED_BACKOFF_SEC * (2 ** attempt))
                continue

            self._bump('fail_http_4xx')
            return None, 'feed_http_error'

        return None, 'feed_network_failure'              # pragma: no cover


class AirportWeatherFeed(_HttpFeed):
    """METAR observations from aviationweather.gov. THE resolution-relevant one.

    METAR reports temperature in whole or half degrees CELSIUS, so a converted
    Fahrenheit value carries about 0.9F of quantisation. On a market whose
    threshold is a whole number of degrees F that quantisation is material near
    the line, which is why every row also carries `airport_temp_c`.
    """

    url = AVIATION_WEATHER_URL
    source_name = 'airport_metar'

    def observation(self, icao: str) -> Tuple[Optional[Reading], str]:
        payload, status = self._get_json({'ids': str(icao).upper(),
                                          'format': 'json'})
        if payload is None:
            return None, status

        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, dict):
            rows = payload.get('data') or []
        else:
            return None, 'airport_unexpected_shape'
        if not rows:
            # The station reported nothing, or the ICAO is wrong. Either way we
            # have no observation, and no observation is not a temperature.
            return None, 'airport_no_observation'

        row = rows[0]
        if not isinstance(row, dict):
            return None, 'airport_unexpected_shape'

        try:
            temp_c = float(row.get('temp'))
        except (TypeError, ValueError):
            return None, 'airport_no_temperature_field'
        if not math.isfinite(temp_c):
            # convention 19: a NaN sails straight through float() and poisons
            # every average it touches downstream.
            return None, 'airport_non_finite_temperature'

        obs_ts = row.get('obsTime')
        if obs_ts is None:
            obs_ts = row.get('reportTime')
        try:
            observed = int(float(obs_ts))
        except (TypeError, ValueError):
            observed = _parse_iso_seconds(obs_ts)

        return Reading(source=self.source_name,
                       station=str(row.get('icaoId') or icao).upper(),
                       temp_f=c_to_f(temp_c),
                       observed_ts=observed,
                       raw=row.get('rawOb')), 'ok'


class DowntownWeatherFeed(_HttpFeed):
    """Current temperature for a downtown lat/lon, from open-meteo.

    This is the CONSUMER-APP ANCHOR, and it is diagnostic only. Nothing in the
    entry gate reads it. It exists so that every row carries the gap this
    strategy's whole thesis rests on, which is the only route by which the 3-8F
    claim ever becomes a measurement instead of staying a rumour.

    open-meteo returns a MODEL grid cell, not a station observation. That is
    exactly what a phone app shows, which is the point, but it must never be
    confused with a reading that anything resolves on.
    """

    url = OPEN_METEO_URL
    source_name = 'downtown_open_meteo'

    def observation(self, lat: float,
                    lon: float) -> Tuple[Optional[Reading], str]:
        payload, status = self._get_json({
            'latitude': lat, 'longitude': lon,
            'current': 'temperature_2m',
            'temperature_unit': 'fahrenheit',
        })
        if payload is None:
            return None, status
        if not isinstance(payload, dict):
            return None, 'downtown_unexpected_shape'
        current = payload.get('current')
        if not isinstance(current, dict):
            return None, 'downtown_no_current_block'
        try:
            temp_f = float(current.get('temperature_2m'))
        except (TypeError, ValueError):
            return None, 'downtown_no_temperature_field'
        if not math.isfinite(temp_f):
            return None, 'downtown_non_finite_temperature'
        return Reading(source=self.source_name,
                       station='{:.4f},{:.4f}'.format(float(lat), float(lon)),
                       temp_f=temp_f,
                       observed_ts=_parse_iso_seconds(current.get('time')),
                       raw=None), 'ok'


# ---------------------------------------------------------------------------
# Rules text, thresholds, discovery
# ---------------------------------------------------------------------------

def rules_text(market) -> str:
    """Join every field on a Gamma market that could carry the rules.

    Returns '' when the market carries no rules text at all, which is a refusal
    upstream and never a licence to fall back on the station table.
    """
    raw = getattr(market, 'raw', None)
    if not isinstance(raw, dict):
        return ''
    parts: List[str] = []
    for key in RULES_TEXT_FIELDS:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value)
    return '\n'.join(parts)


def stations_named_in(text: str) -> List[str]:
    """Every distinct ICAO the text names IN PROSE, via `STATION_ALIASES`.

    Four-letter codes are matched on a word boundary so `KDEN` cannot fire
    inside an unrelated token. Multi-word station names are matched as plain
    substrings, where a boundary regex buys nothing.

    This route is alias-gated on purpose: a bare four-letter word in prose is
    not self-evidently a station, so a code we do not already know reads as
    unreadable rather than as a station. `stations_in_urls` is the opposite
    case and is not alias-gated; see there for why.
    """
    lowered = str(text or '').lower()
    found = set()
    for alias, icao in STATION_ALIASES.items():
        if len(alias) == 4 and alias.isalpha():
            if re.search(r'\b' + re.escape(alias) + r'\b', lowered):
                found.add(icao)
        elif alias in lowered:
            found.add(icao)
    return sorted(found)


#: An ICAO sitting in a RESOLUTION URL. This is the form every real Polymarket
#: temperature market uses and the form the alias table could never cover:
#:
#:     https://www.wunderground.com/history/daily/us/ny/new-york-city/KLGA
#:     https://www.weather.gov/wrh/timeseries?site=LTFM
#:
#: Measured over 1,727 live markets this yields 50 distinct codes and ZERO
#: false positives, and it is why coverage went from 132 markets to 1,661.
#:
#: Unlike `stations_named_in` this is NOT alias-gated, and that is a deliberate
#: difference rather than an oversight. A four-letter uppercase token in the
#: final path segment of the market's own resolution link, or in its `site=`
#: parameter, IS the station identifier - the URL is the market telling us
#: where to look, not prose we are interpreting. Requiring UPPERCASE and a
#: URL-shaped prefix is what keeps `/history/` and `/daily/` out.
_ICAO_IN_URL_RE = re.compile(
    r'(?:/|site=|station=|stid=|sid=)(?P<icao>[A-Z]{4})(?![A-Za-z0-9])')


def stations_in_urls(text: str) -> List[str]:
    """Every distinct ICAO the text names inside a resolution URL. Sorted."""
    return sorted(set(_ICAO_IN_URL_RE.findall(str(text or ''))))


def all_stations_named_in(text: str) -> List[str]:
    """Union of the prose route and the URL route. Sorted."""
    return sorted(set(stations_named_in(text)) | set(stations_in_urls(text)))


def resolution_station_checked(market,
                               allow_fallback: bool = False
                               ) -> Tuple[Optional[str], str]:
    """Which station does this market SAY it resolves on?

    Returns `(icao, 'ok')`, `(icao, 'ok_from_city_fallback_table')`,
    `(None, 'resolution_station_unknown')` or
    `(None, 'resolution_station_ambiguous')`.

    THE RULES TEXT WINS. Both the URL route and the prose route read the
    market's own text, and the fallback table is consulted ONLY when the text
    named nothing at all. When the text names something, the table cannot
    override it and cannot even be reached.

    A station named in prose but absent from `STATION_ALIASES` reads as
    `resolution_station_unknown`. That is the honest answer: we could not read
    it, so we do not trade it. It is NOT "there is no station".

    Two DIFFERENT stations in one rules text is `resolution_station_ambiguous`
    and its own cause. Picking the first is a coin flip on the whole position.
    Note the two routes agreeing is not ambiguity: the NYC rules text says both
    "LaGuardia Airport Station" and `.../new-york-city/KLGA`, which is one
    station named twice, and the union is taken before the count.
    """
    text = rules_text(market)
    if text.strip():
        found = all_stations_named_in(text)
        if len(found) > 1:
            return None, 'resolution_station_ambiguous'
        if found:
            return found[0], 'ok'
    if allow_fallback:
        city = city_name_from_question(market)
        icao = CITY_STATION_FALLBACK.get(city or '')
        if icao is not None:
            return icao, 'ok_from_city_fallback_table'
    return None, 'resolution_station_unknown'


def city_for_market(market) -> Optional[str]:
    """Which `WEATHER_MARKETS` key this market's slug looks like, or None.

    Used ONLY to pick a downtown lat/lon, to shortlist markets in discovery, and
    to cross-check the station table against the rules text. Never used to
    decide resolution.
    """
    slug = str(getattr(market, 'slug', '') or '').lower()
    for key, city in WEATHER_MARKETS.items():
        for pattern in city['slug_patterns']:
            if pattern in slug:
                return key
    question = str(getattr(market, 'question', '') or '').lower()
    for key, city in WEATHER_MARKETS.items():
        for alias in city['aliases']:
            if len(alias) == 4 and alias.isalpha():
                continue
            if alias.lower() in question:
                return key
    return None


#: "Will the highest temperature in Seoul (Incheon) be 22C on August 18?" ->
#: "seoul". The parenthetical is the airport, not the city, and keeping it
#: would split one city across two report buckets.
_CITY_IN_QUESTION_RE = re.compile(
    r'temperature\s+in\s+(?P<city>.+?)\s+be\b', re.IGNORECASE)


def city_name_from_question(market) -> Optional[str]:
    """The city a ladder question names, lowercased, or None.

    Separate from `city_for_market` and NOT a replacement for it. This one is
    for REPORTING (the `by_city` breakdown) and for the fallback station table,
    both of which need all 51 cities Polymarket lists. `city_for_market` keys
    into `WEATHER_MARKETS`, which carries downtown coordinates for five cities
    only, and is the one used to pick a downtown grid cell. Collapsing them
    would mean either inventing coordinates or losing 46 cities from the
    report, so they stay two functions with two jobs.
    """
    question = str(getattr(market, 'question', '') or '')
    match = _CITY_IN_QUESTION_RE.search(question)
    if match is None:
        return None
    city = re.sub(r'\s*\([^)]*\)', '', match.group('city')).strip().lower()
    return city or None


#: What the resolution source's own rules text says about REPORTING PRECISION.
#: Live text, all three variants:
#:     "measures temperatures to whole degrees Celsius (eg, 9C)"      1,419
#:     "measures temperatures to whole degrees Fahrenheit (eg, 21F)"    286
#:     "measures temperatures in Celsius to one decimal place"           66
_REPORTING_PRECISION_RE = re.compile(
    r'measures\s+temperatures?\s*'
    r'(?:in\s+(?:celsius|fahrenheit|centigrade)\s*)?'
    r'to\s+(?P<prec>whole\s+degrees?|one\s+decimal\s+place)',
    re.IGNORECASE)


def reporting_step_checked(market) -> Tuple[Optional[float], str]:
    """How finely does the resolution source report, in NATIVE degrees?

    Returns `(1.0, 'ok')`, `(0.1, 'source_precision_finer_than_ladder_step')`
    or `(None, 'source_reporting_precision_unknown')`.

    THIS GATE EXISTS BECAUSE THE LADDER EDGES DEPEND ON IT. A rung labelled 84
    means "the reported value is 84". When the source reports whole degrees,
    reported == round(true), so the rung is exactly [83.5, 84.5) and the whole
    eleven-rung ladder tiles the line with no gap and no overlap. When the
    source reports to one decimal place - which the 66 Hong Kong markets do -
    that identity fails and Polymarket has not published where the rung edges
    sit. [26.5, 27.5) and [27.0, 28.0) are both readings of "27C", they differ
    by half a degree, and half a degree against a 0.75F sigma floor is most of
    the claimed edge. So those markets are REFUSED under their own reason
    rather than priced on a guessed edge.

    An unstated precision is also a refusal, and a separate one. Measured, all
    1,771 live city markets state theirs, so this costs nothing today and is
    the gate that catches Polymarket quietly changing the wording.
    """
    text = rules_text(market)
    if not text.strip():
        return None, 'source_reporting_precision_unknown'
    match = _REPORTING_PRECISION_RE.search(text)
    if match is None:
        return None, 'source_reporting_precision_unknown'
    if 'whole' in match.group('prec').lower():
        return 1.0, 'ok'
    return 0.1, 'source_precision_finer_than_ladder_step'


#: Comparison words, split by which way Yes has to go. `at most` and `at least`
#: are whole phrases because "at" alone is meaningless.
_BELOW_WORDS = frozenset(('below', 'under', 'less than', 'lower than',
                          'at most'))
_ABOVE_WORDS = frozenset(('exceed', 'exceeds', 'above', 'over', 'at least',
                          'greater than', 'higher than'))

#: The POSTFIX comparators, which is the form every live market uses:
#: "84F or below". Kept separate from the prefix words because the two forms
#: mean different things - see `parse_threshold_checked`.
_POSTFIX_BELOW_WORDS = frozenset(('below', 'lower', 'less', 'under', 'colder'))
_POSTFIX_ABOVE_WORDS = frozenset(('higher', 'above', 'more', 'greater', 'over',
                                  'warmer'))

#: U+00B0 DEGREE SIGN and U+00BA MASCULINE ORDINAL. Polymarket ships both, and
#: the second one is not a degree sign at all - it is the character the global
#: temperature family uses. A unit regex that knows only U+00B0 reads
#: "1.29ºC" as a bare number.
_DEGREE_CHARS = '°º'

#: The unit suffix, OPTIONAL in the pattern so that "no unit" arrives as a
#: distinct outcome rather than as "no match". Two drop causes, two numbers
#: (convention 20): `no_threshold_pattern` and `unit_missing_or_ambiguous` are
#: completely different problems and pooling them would hide whichever is
#: smaller.
_UNIT_SUFFIX = (r'\s*(?:degrees?\s*)?(?:[' + _DEGREE_CHARS + r']\s*)?'
                r'(?P<unit>fahrenheit|celsius|centigrade|f|c)?\b')

_NUM = r'-?\d+(?:\.\d+)?'

#: "be between 76-77F". The Fahrenheit ladders are TWO degrees wide per rung,
#: which the Celsius ladders are not. Separator covers the hyphen, U+2013 EN
#: DASH, U+2014 EM DASH (escaped, never written literally - house rule), "to"
#: and "and".
_RANGE_RE = re.compile(
    r'between\s+(?P<lo>' + _NUM + r')\s*(?:-|\u2013|\u2014|to|and)\s*'
    r'(?P<hi>' + _NUM + r')' + _UNIT_SUFFIX, re.IGNORECASE)

#: "84F or below", "33C or higher". THE form that broke the original parser.
_POSTFIX_RE = re.compile(
    r'(?P<value>' + _NUM + r')' + _UNIT_SUFFIX +
    r'\s*or\s+(?P<post>below|lower|less|under|colder|higher|above|more|'
    r'greater|over|warmer)\b', re.IGNORECASE)

#: "be 84F" with nothing after it: the exact-degree rung.
_BUCKET_RE = re.compile(
    r'\bbe\s+(?P<value>' + _NUM + r')' + _UNIT_SUFFIX + r'(?!\s*or\s)',
    re.IGNORECASE)

#: The legacy PREFIX form, "exceed 85F". No live Polymarket market uses it, but
#: it is a perfectly ordinary way to write a threshold and dropping support
#: would be a regression for no gain.
_THRESHOLD_RE = re.compile(
    r'\b(?P<word>exceeds|exceed|above|over|at least|greater than|higher than|'
    r'below|under|less than|lower than|at most)\s*'
    r'(?P<value>' + _NUM + r')' + _UNIT_SUFFIX,
    re.IGNORECASE)

#: Every unit marker in the question, used to catch a question that names BOTH.
#: A question carrying "C" in one place and "F" in another is not a question we
#: get to pick a side of.
_ANY_UNIT_RE = re.compile(
    r'' + _NUM + r'\s*(?:degrees?\s*)?(?:[' + _DEGREE_CHARS + r']\s*)?'
    r'(fahrenheit|celsius|centigrade|f|c)\b', re.IGNORECASE)

#: A number with an explicit TEMPERATURE unit marker. Used only by discovery:
#: "Will BTC exceed 50000?" parses as a threshold under a unit-free regex, and
#: shortlisting it as a weather market would be a false positive that costs a
#: station lookup and a confusing skip row.
_TEMP_UNIT_MARKER_RE = re.compile(
    r'' + _NUM + r'\s*(?:[' + _DEGREE_CHARS + r']\s*)?'
    r'(?:f\b|c\b|degrees\b|fahrenheit\b|celsius\b)', re.IGNORECASE)

#: The OTHER market family under the weather tag. A planetary anomaly index,
#: not a city airport station, and not something any METAR reading prices.
_GLOBAL_TEMPERATURE_RE = re.compile(
    r'global\s+temperature|global\s+average\s+temperature|'
    r'temperature\s+anomaly', re.IGNORECASE)

#: Every outcome `parse_threshold_checked` can report. One string per cause.
THRESHOLD_PARSE_REASONS = (
    'ok',
    'question_empty',
    'global_temperature_market',
    'no_threshold_pattern',
    'unit_missing_or_ambiguous',
    'non_integer_ladder_rung',
    'range_bounds_inverted',
)

#: `kind` values that describe a LADDER rung, meaning the edges were derived
#: from a half-step and therefore depend on the source reporting whole degrees.
LADDER_KINDS = ('at_or_below', 'at_or_above', 'exact_bucket', 'range_bucket')


def is_global_temperature_market(question) -> bool:
    """True for the planetary-anomaly family, which is NOT a city market."""
    if not isinstance(question, str):
        return False
    return _GLOBAL_TEMPERATURE_RE.search(question) is not None


def _normalise_unit(token) -> Optional[str]:
    """'c' / 'Celsius' / 'centigrade' -> 'C'. 'f' / 'Fahrenheit' -> 'F'."""
    if not token:
        return None
    head = str(token).strip().lower()[0]
    if head == 'c':
        return 'C'
    if head == 'f':
        return 'F'
    return None                              # pragma: no cover - regex guards


def _units_in(question: str) -> set:
    """Every distinct unit the question names. More than one is ambiguous."""
    return {_normalise_unit(tok) for tok in _ANY_UNIT_RE.findall(question)} - {
        None}


def to_fahrenheit(value: Optional[float], unit: str) -> Optional[float]:
    """Convert a NATIVE-unit temperature to Fahrenheit. None passes through.

    None means "this edge is unbounded", and an unbounded edge stays unbounded
    in any unit. Sending it through `c_to_f` would turn it into 32.0.
    """
    if value is None:
        return None
    return float(value) if unit == 'F' else c_to_f(value)


@dataclass(frozen=True)
class Threshold:
    """A parsed temperature threshold, as a HALF-OPEN INTERVAL `[lo_f, hi_f)`.

    Yes resolves true when the resolution reading lands inside the interval.
    `None` on either edge means unbounded on that side, so the two ladder tails
    and the interior buckets are the same object with different edges.

    The public shape the rest of the repo already reads is preserved:

        value_f     the single number a human would call "the threshold", in
                    FAHRENHEIT. For a tail that is the decision boundary. For a
                    bucket it is the rung CENTRE, which is not a boundary, so
                    `kind` is on every row and must be read alongside it.
        above       True / False for a tail, None for a bucket, where "neither
                    above nor below" is the honest answer.
        matched_text the substring that produced all of this.

    `native_lo` / `native_hi` are the SAME interval in the market's own unit,
    kept so a log row can be checked by eye against the question text without
    anyone having to run the conversion backwards. That is not decoration: a
    Celsius bucket whose F edges look 1.8 apart instead of 1.0 is either
    correct or a bug, and the native pair is what tells the two apart.
    """

    value_f: Optional[float]
    above: Optional[bool]
    matched_text: str
    kind: str = ''
    unit: str = 'F'
    lo_f: Optional[float] = None
    hi_f: Optional[float] = None
    native_lo: Optional[float] = None
    native_hi: Optional[float] = None
    ladder_step_native: Optional[float] = None

    def __post_init__(self):
        # Back-compatible construction: `Threshold(85.0, True, 'exceed 85F')`
        # still means "Yes needs above 85F" and fills its own interval in.
        if not self.kind:
            if self.above is True:
                object.__setattr__(self, 'kind', 'at_or_above')
            elif self.above is False:
                object.__setattr__(self, 'kind', 'at_or_below')
            else:
                object.__setattr__(self, 'kind', 'exact_bucket')
        if self.lo_f is None and self.hi_f is None and self.value_f is not None:
            if self.kind == 'at_or_above':
                object.__setattr__(self, 'lo_f', float(self.value_f))
            elif self.kind == 'at_or_below':
                object.__setattr__(self, 'hi_f', float(self.value_f))

    @property
    def is_ladder_rung(self) -> bool:
        """True when the edges came from a half-step off an integer rung."""
        return self.ladder_step_native is not None

    def to_dict(self) -> dict:
        def r(value):
            return None if value is None else round(float(value), 4)

        return {'threshold_f': self.value_f,
                'yes_needs_above': self.above,
                'threshold_matched_text': self.matched_text,
                'threshold_kind': self.kind,
                'threshold_unit': self.unit,
                'threshold_lo_f': r(self.lo_f),
                'threshold_hi_f': r(self.hi_f),
                'threshold_native_lo': r(self.native_lo),
                'threshold_native_hi': r(self.native_hi),
                'threshold_is_ladder_rung': self.is_ladder_rung,
                'ladder_step_native': self.ladder_step_native}


def _ladder(matched_text: str, unit: str, native_lo: Optional[float],
            native_hi: Optional[float], kind: str, step: float,
            value_native: float, above: Optional[bool]) -> Threshold:
    """Build a ladder rung. THE CONVERSION HAPPENS HERE AND ONLY HERE.

    `native_lo` and `native_hi` arrive already widened by the half-step IN THE
    MARKET'S OWN UNIT, and are converted afterwards. Widening in Fahrenheit
    instead would make every Celsius rung 1.0F wide where it should be 1.8F, a
    44% understatement of the bucket that shows up as a fabricated edge on
    every interior rung of every Celsius ladder. There is a test that pins the
    ordering rather than the result, because the result is right by accident
    under either ordering when the unit is already Fahrenheit.
    """
    return Threshold(value_f=to_fahrenheit(value_native, unit),
                     above=above,
                     matched_text=matched_text,
                     kind=kind,
                     unit=unit,
                     lo_f=to_fahrenheit(native_lo, unit),
                     hi_f=to_fahrenheit(native_hi, unit),
                     native_lo=native_lo,
                     native_hi=native_hi,
                     ladder_step_native=step)


def parse_threshold_checked(question) -> Tuple[Optional[Threshold], str]:
    """Pull the threshold out of a market question, WITH a refusal reason.

    `(Threshold, 'ok')` or `(None, reason)`, reason from
    `THRESHOLD_PARSE_REASONS`. There is deliberately NO default anywhere in
    here: a strategy that falls back to a guessed threshold, or a guessed UNIT,
    when it cannot read the real one is trading a number it invented, and on a
    binary that is the whole position.

    Four forms, tried in this order because the later ones are prefixes of the
    earlier ones:

        1. "be between 76-77F"   range bucket   [75.5, 77.5)
        2. "84F or below"        tail           (-inf, 84.5)
           "88F or higher"       tail           [87.5, +inf)
        3. "exceed 85F"          legacy prefix  [85.0, +inf)
        4. "be 84F"              exact bucket   [83.5, 84.5)

    THE HALF-STEP IS ONLY ON THE LADDER FORMS, and that asymmetry is the point,
    not an inconsistency. A ladder rung is a statement about the REPORTED
    value, which the rules text says is a whole number of degrees, so
    "reported <= 84" is "true temperature < 84.5". A prefix comparison
    ("exceed 85F") is a statement about the temperature itself and has no
    rounding step in it. Both are correct for their own form, and the row
    carries `threshold_is_ladder_rung` so a reader never has to guess which
    they are looking at.

    "Highest temperature in NYC on August 18?" carries no threshold at all and
    correctly returns `no_threshold_pattern`. That is a scalar market, not a
    binary this strategy can price.
    """
    if not isinstance(question, str) or not question.strip():
        return None, 'question_empty'
    if is_global_temperature_market(question):
        return None, 'global_temperature_market'

    units = _units_in(question)
    if len(units) > 1:
        return None, 'unit_missing_or_ambiguous'

    match = _RANGE_RE.search(question)
    if match is not None:
        unit = _normalise_unit(match.group('unit'))
        if unit is None:
            return None, 'unit_missing_or_ambiguous'
        lo, hi = float(match.group('lo')), float(match.group('hi'))
        if not (float(lo).is_integer() and float(hi).is_integer()):
            return None, 'non_integer_ladder_rung'
        if hi < lo:
            return None, 'range_bounds_inverted'
        return _ladder(match.group(0).strip(), unit, lo - 0.5, hi + 0.5,
                       'range_bucket', float(hi - lo + 1.0),
                       (lo + hi) / 2.0, None), 'ok'

    match = _POSTFIX_RE.search(question)
    if match is not None:
        unit = _normalise_unit(match.group('unit'))
        if unit is None:
            return None, 'unit_missing_or_ambiguous'
        value = float(match.group('value'))
        if not float(value).is_integer():
            return None, 'non_integer_ladder_rung'
        word = match.group('post').lower()
        if word in _POSTFIX_BELOW_WORDS:
            return _ladder(match.group(0).strip(), unit, None, value + 0.5,
                           'at_or_below', 1.0, value + 0.5, False), 'ok'
        return _ladder(match.group(0).strip(), unit, value - 0.5, None,
                       'at_or_above', 1.0, value - 0.5, True), 'ok'

    match = _THRESHOLD_RE.search(question)
    if match is not None:
        unit = _normalise_unit(match.group('unit'))
        if unit is None:
            return None, 'unit_missing_or_ambiguous'
        value = float(match.group('value'))
        if not math.isfinite(value):         # pragma: no cover - regex guards
            return None, 'no_threshold_pattern'
        above = match.group('word').lower() in _ABOVE_WORDS
        value_f = to_fahrenheit(value, unit)
        return Threshold(value_f=value_f, above=above,
                         matched_text=match.group(0).strip(),
                         kind='at_or_above' if above else 'at_or_below',
                         unit=unit,
                         lo_f=value_f if above else None,
                         hi_f=None if above else value_f,
                         native_lo=value if above else None,
                         native_hi=None if above else value,
                         ladder_step_native=None), 'ok'

    match = _BUCKET_RE.search(question)
    if match is not None:
        unit = _normalise_unit(match.group('unit'))
        if unit is None:
            return None, 'unit_missing_or_ambiguous'
        value = float(match.group('value'))
        if not float(value).is_integer():
            return None, 'non_integer_ladder_rung'
        return _ladder(match.group(0).strip(), unit, value - 0.5, value + 0.5,
                       'exact_bucket', 1.0, value, None), 'ok'

    return None, 'no_threshold_pattern'


def parse_threshold(question) -> Optional[Threshold]:
    """`parse_threshold_checked` without the reason. None means unparseable."""
    threshold, _reason = parse_threshold_checked(question)
    return threshold


def market_metric(question) -> Optional[str]:
    """'daily_high' / 'daily_low' / None.

    Recorded on every row, never gated on. It exists because the two ladders
    are biased in OPPOSITE directions by the point-in-time model (see the
    module docstring), and a pooled win rate across both would average the two
    biases into a number that looks unbiased and is not.
    """
    text = str(question or '').lower()
    if 'highest temperature' in text:
        return 'daily_high'
    if 'lowest temperature' in text:
        return 'daily_low'
    return None


def looks_like_a_temperature_market(market) -> bool:
    """Discovery shortlist. Deliberately WIDER than `parse_threshold`.

    A real Polymarket question reads "Will NYC exceed 85F on August 18?" and
    contains none of `TEMPERATURE_KEYWORDS`, so a keyword-only filter drops the
    exact markets this strategy exists for. Three independent signals, any of
    which shortlists:

      1. a `TEMPERATURE_KEYWORDS` word in the question
      2. the slug or question maps onto a `WEATHER_MARKETS` city
      3. the question carries a number with an explicit temperature unit

    The ONE hard exclusion is the global temperature family, which carries the
    word "temperature" and would sail through signal 1. It is not a city
    airport market at any stage, so excluding it here is not a shortcut past a
    later gate.

    This is a SHORTLIST, not a resolution decision. Everything shortlisted still
    has to survive `resolution_station_checked` and `parse_threshold` before a
    single share is priced, so a false positive here costs a skip row, never a
    trade.
    """
    raw_question = str(getattr(market, 'question', '') or '')
    if is_global_temperature_market(raw_question):
        return False
    question = raw_question.lower()
    if any(k in question for k in TEMPERATURE_KEYWORDS):
        return True
    if city_for_market(market) is not None:
        return True
    return _TEMP_UNIT_MARKER_RE.search(question) is not None


#: Every reason `find_weather_markets` can drop a raw Gamma market. One string
#: per cause and the accounting identity is asserted, so a market that vanishes
#: has a name attached rather than being a silent `continue` (convention 20).
DISCOVERY_DROP_REASONS = (
    'event_not_a_dict',
    'market_not_a_dict',
    'market_unbuildable',
    'closed',
    'inactive',
    'not_accepting_orders',
    'end_date_missing',
    'end_date_past',
    'not_a_temperature_market',
    'duplicate_across_tags',
    'over_limit',
)


def _gamma_events_page(client, tag_slug: str, offset: int,
                       limit: int) -> Tuple[Optional[list], str]:
    """One page of `/events` for a tag. `(events, 'ok')` or `(None, reason)`.

    Goes through `client.gamma` and nothing else. A raw socket or a bare
    `requests.get` here would bypass the client's timeout, retry and header
    handling, and would be the second place in the repo that knows the Gamma
    host.

    Gamma returns a bare list on this route and a `{'events': [...]}` wrapper
    on others. Both are accepted because the client is not the only thing that
    ever changes shape, and a shape we do not recognise is `unexpected_shape`
    rather than an empty page - an empty page would end pagination early and
    silently truncate the universe.
    """
    payload = client.gamma(GAMMA_EVENTS_PATH, {
        'tag_slug': tag_slug,
        'closed': 'false',
        'limit': limit,
        'offset': offset,
    })
    if payload is None:
        return None, 'read_failed'
    if isinstance(payload, list):
        return payload, 'ok'
    if isinstance(payload, dict):
        events = payload.get('events')
        if events is None:
            events = payload.get('data')
        if isinstance(events, list):
            return events, 'ok'
    return None, 'unexpected_shape'


def find_weather_markets(client, limit: Optional[int] = None,
                         now: Optional[float] = None) -> Dict[str, object]:
    """Find live temperature markets on Gamma, via the TAG route.

    Returns `{'ok', 'markets', 'by_city', 'raw_count', 'searched', 'reason'}`
    plus `drops`, `pages`, `truncated` and `pagination_capped` for accounting.

    Three outcomes that must never be pooled (convention 11):

        ok=False, reason='read_failed'        Gamma was unreachable. NOT a
                                              result, and NOT an empty market
                                              list.
        ok=True,  reason='no_weather_market'  Gamma answered and nothing
                                              matched. A cannot-run, still not
                                              a result.
        ok=True,  reason=None                 markets found.

    A run that found nothing because the API was down and a run that found
    nothing because Polymarket listed no temperature markets that day are the
    same empty list and completely different facts.

    WHY THE TAG ROUTE AND NOT `/public-search`. Measured 2026-08-18, the search
    route returned 33 markets and the tag route returned 2,045. The search route
    did not fail; it answered, and its answer was 1.6% of the universe. That is
    the worst possible failure mode because it looks exactly like a quiet day.

    STALE MARKETS ARE DROPPED HERE, not left for `evaluate`. A market whose
    `endDate` has passed cannot be entered, and letting it through would spend
    a METAR fetch and emit a `market_past_resolution_time` row for every rung of
    every yesterday. A market with NO `endDate` is dropped too, under its own
    reason: we cannot tell a fresh one from a stale one, and that is not the
    same fact as knowing it is stale.
    """
    now = time.time() if now is None else float(now)
    seen: Dict[str, object] = {}
    drops: Dict[str, int] = {}
    unbuildable: Dict[str, int] = {}
    raw_count = 0
    pages = 0
    any_ok = False
    read_failures: Dict[str, int] = {}
    pagination_capped = False

    def drop(reason: str) -> None:
        drops[reason] = drops.get(reason, 0) + 1

    for tag_slug in WEATHER_TAG_SLUGS:
        offset = 0
        for _page in range(GAMMA_MAX_PAGES):
            events, status = _gamma_events_page(client, tag_slug, offset,
                                                GAMMA_PAGE_LIMIT)
            if events is None:
                read_failures[status] = read_failures.get(status, 0) + 1
                break
            any_ok = True
            pages += 1
            for event in events:
                if not isinstance(event, dict):
                    drop('event_not_a_dict')
                    raw_count += 1
                    continue
                for raw in event.get('markets') or ():
                    raw_count += 1
                    if not isinstance(raw, dict):
                        drop('market_not_a_dict')
                        continue
                    keep, reason, payload = _discovery_keep(raw, now)
                    if not keep:
                        drop(reason)
                        if reason == 'market_unbuildable':
                            # `payload` is the sub-reason from
                            # `market_from_gamma_checked`. Counted separately
                            # so "44 unbuildable" never hides four different
                            # malformed-payload bugs behind one number.
                            key = str(payload)
                            unbuildable[key] = unbuildable.get(key, 0) + 1
                        continue
                    market = payload
                    key = market.slug or market.id
                    if key in seen:
                        drop('duplicate_across_tags')
                        continue
                    if limit and len(seen) >= limit:
                        drop('over_limit')
                        continue
                    seen[key] = market
            if len(events) < GAMMA_PAGE_LIMIT:
                break
            offset += GAMMA_PAGE_LIMIT
        else:
            pagination_capped = True

    if not any_ok:
        return {'ok': False, 'markets': [], 'by_city': {}, 'raw_count': 0,
                'searched': list(WEATHER_TAG_SLUGS), 'drops': {},
                'unbuildable': {}, 'pages': 0, 'truncated': False,
                'pagination_capped': False, 'read_failures': read_failures,
                'reason': 'read_failed'}

    markets = list(seen.values())
    by_city: Dict[str, list] = {}
    for market in markets:
        key = (city_name_from_question(market) or city_for_market(market)
               or 'unmapped')
        by_city.setdefault(key, []).append(market)

    # Convention 20: every raw market is either kept or counted under exactly
    # one named cause. An assert here is cheap and it is the only thing that
    # would catch a `continue` added later without a counter next to it.
    accounted = len(markets) + sum(drops.values())
    assert accounted == raw_count, (accounted, raw_count, drops)

    return {'ok': True, 'markets': markets, 'by_city': by_city,
            'raw_count': raw_count, 'searched': list(WEATHER_TAG_SLUGS),
            'drops': drops, 'unbuildable': unbuildable, 'pages': pages,
            'truncated': bool(drops.get('over_limit')),
            'pagination_capped': pagination_capped,
            'read_failures': read_failures,
            'reason': None if markets else 'no_weather_market'}


def _discovery_keep(raw: dict, now: float) -> Tuple[bool, str, object]:
    """One raw Gamma market -> `(keep, reason, market_or_unbuildable_reason)`.

    Split out of `find_weather_markets` so the filter order is readable in one
    screen. Order matters for ATTRIBUTION, not for the outcome: a closed market
    that is also stale is counted once, as closed.
    """
    from engine.polymarket.markets import market_from_gamma_checked

    if raw.get('closed'):
        return False, 'closed', None
    if not raw.get('active', True):
        return False, 'inactive', None
    if raw.get('acceptingOrders') is False:
        return False, 'not_accepting_orders', None

    end_ts = _parse_iso_seconds(raw.get('endDate'))
    if end_ts is None:
        return False, 'end_date_missing', None
    if end_ts <= now:
        return False, 'end_date_past', None

    market, build_reason = market_from_gamma_checked(raw)
    if market is None:
        return False, 'market_unbuildable', build_reason
    if not looks_like_a_temperature_market(market):
        return False, 'not_a_temperature_market', None
    return True, 'ok', market


# ---------------------------------------------------------------------------
# The strategy
# ---------------------------------------------------------------------------

class WeatherArb(PolymarketStrategy):
    """Price a temperature binary off the AIRPORT station the rules name.

    Enters only when all of these hold:

      1. the question is not the global temperature family
      2. the market's own rules text names one station we can read and fetch
      3. the question carries a parseable threshold WITH AN EXPLICIT UNIT
      4. a ladder rung's source reports whole degrees, so its edges are exact
      5. the market is not a daily extreme, which this model cannot price
      6. the METAR observation is under an hour old
      7. our probability and the market's implied direction DISAGREE
      8. the walked entry price still leaves at least MIN_EDGE

    ON LIVE DATA, GATE 5 REFUSES EVERYTHING. Every Polymarket temperature
    market measured on 2026-08-18 resolves on a daily high or a daily low, so
    with `allow_daily_extreme_markets=False` this strategy books nothing. The
    parser fix that got the other seven gates working is real and measurable
    (1,727 of 1,739 questions parse, 1,661 resolve to a station, 1,203 reach
    the orderbook); gate 5 is the honest verdict on what to do with them.

    Holds to resolution. The stop is 0.00, which on a binary is the exact floor
    and satisfies convention 8.
    """

    strategy_name = 'PM_weather_arb'
    paper_mode = PAPER_MODE

    #: Holds to resolution. There is no exit model here; see the docstring.
    manages_exits = False

    def __init__(self, airport_feed=None, downtown_feed=None,
                 min_edge: float = MIN_EDGE,
                 max_obs_age_sec: float = MAX_OBS_AGE_SEC,
                 sigma_floor_f: float = SIGMA_FLOOR_F,
                 sigma_per_sqrt_hour_f: float = SIGMA_PER_SQRT_HOUR_F,
                 max_hours_to_resolution: float = MAX_HOURS_TO_RESOLUTION,
                 target_shares: int = TARGET_SHARES,
                 max_notional_usdc: float = MAX_NOTIONAL_USDC,
                 min_shares: int = MIN_SHARES,
                 fetch_downtown: bool = True,
                 allow_station_fallback: bool = False,
                 allow_daily_extreme_markets: bool = False):
        #: Both feeds are built LAZILY. Constructing an AirportWeatherFeed
        #: imports `requests`; a test that injects fakes, or a caller that only
        #: wants `parse_threshold`, should not need a network stack.
        self._airport_feed = airport_feed
        self._downtown_feed = downtown_feed
        self.min_edge = min_edge
        self.max_obs_age_sec = max_obs_age_sec
        self.sigma_floor_f = sigma_floor_f
        self.sigma_per_sqrt_hour_f = sigma_per_sqrt_hour_f
        self.max_hours_to_resolution = max_hours_to_resolution
        self.target_shares = target_shares
        self.max_notional_usdc = max_notional_usdc
        self.min_shares = min_shares
        #: The downtown reading is diagnostic only and costs a second HTTP round
        #: trip. Turning it off loses the gap measurement, which is the only
        #: thing that would ever turn the 3-8F claim into a number, so the
        #: default is on and every row records which way it was set.
        self.fetch_downtown = bool(fetch_downtown)
        #: OFF by default, and that default is the whole point. Turning it on
        #: lets `CITY_STATION_FALLBACK` supply a station for a market whose
        #: rules text names none, which is the one thing this file refuses to
        #: do everywhere else. Measured, it buys exactly one city (Hong Kong,
        #: 66 markets, 3.7%) and it buys it with an airport 25km from the
        #: station the market actually resolves on. A row that used it is
        #: stamped `station_is_a_fallback_guess=True` so the population can be
        #: scored separately, which is the only condition under which turning
        #: it on is defensible.
        self.allow_station_fallback = bool(allow_station_fallback)
        #: OFF by default, and this one is the reason the strategy books no
        #: live entries today. 100% of the live Polymarket temperature universe
        #: resolves on a DAILY EXTREME, and `probability_yes` prices a single
        #: reading at the settlement timestamp. See gate 2c in `evaluate` for
        #: the two measured examples. Turning it on is for GATHERING TAPE, not
        #: for trading: the rows it produces are model error with a price
        #: attached, and pooling them with anything is how a wrong model
        #: acquires a track record.
        self.allow_daily_extreme_markets = bool(allow_daily_extreme_markets)

    # -- feeds --------------------------------------------------------------

    @property
    def airport_feed(self) -> AirportWeatherFeed:
        if self._airport_feed is None:
            self._airport_feed = AirportWeatherFeed()
        return self._airport_feed

    @property
    def downtown_feed(self) -> DowntownWeatherFeed:
        if self._downtown_feed is None:
            self._downtown_feed = DowntownWeatherFeed()
        return self._downtown_feed

    # -- model --------------------------------------------------------------

    @staticmethod
    def clock(ctx: MarketContext) -> Optional[float]:
        """Absolute seconds for this observation, or None.

        Derived from the context rather than the wall clock, so a decision is
        reproducible from a logged context and a test does not have to mock
        `time`. For a weather market the caller sets `window_ts` to the poll
        second; there is no 5-minute window here to floor to.
        """
        if not ctx.window_ts:
            return None
        return float(ctx.window_ts) + float(ctx.seconds_into_window or 0.0)

    def sigma_f(self, hours_remaining: float) -> float:
        """Standard deviation of the final reading, in degrees F.

        See the module docstring: a diffusion form on a variable with a
        deterministic daily cycle, wrong in a known direction at known times of
        day.
        """
        return self.sigma_floor_f + self.sigma_per_sqrt_hour_f * math.sqrt(
            max(0.0, float(hours_remaining)))

    def probability_yes(self, temp_f: float, threshold: Threshold,
                        hours_remaining: float) -> float:
        """P(the market's Yes side resolves true), from the airport reading.

        `CDF(hi) - CDF(lo)` with either edge allowed to be unbounded, which
        collapses to the old one-sided form on a tail: an `at_or_above` rung has
        `hi_f=None`, so the upper term is 1 and the result is `1 - CDF(lo)`,
        bit-for-bit what the previous two-branch version returned. The bucket
        case is the one that needs both terms, and it is the case that does not
        exist in any older log.

        Reads NOTHING but the interval. A caller that hands it a bucket gets a
        bucket price without having to know it did.
        """
        temp = float(temp_f)
        sigma = self.sigma_f(hours_remaining)
        lo, hi = threshold.lo_f, threshold.hi_f
        if sigma <= 0:                       # pragma: no cover - floor is > 0
            inside = ((lo is None or temp >= lo) and (hi is None or temp < hi))
            return 1.0 if inside else 0.0
        p_hi = 1.0 if hi is None else _NORMAL.cdf((hi - temp) / sigma)
        p_lo = 0.0 if lo is None else _NORMAL.cdf((lo - temp) / sigma)
        # Clamped because floating point can return a hair below zero on a
        # bucket far out in a tail, and a negative probability walks straight
        # into a negative edge and an entry.
        return max(0.0, min(1.0, p_hi - p_lo))

    @staticmethod
    def hours_to_resolution(market, now: float) -> Optional[float]:
        """Hours from `now` until `market.end_date`, or None if unreadable."""
        end_ts = _parse_iso_seconds(getattr(market, 'end_date', None))
        if end_ts is None:
            return None
        return (float(end_ts) - float(now)) / SECONDS_PER_HOUR

    # -- entry --------------------------------------------------------------

    def evaluate(self, ctx: MarketContext) -> Decision:
        """Decide one temperature market. ALWAYS returns a Decision."""
        slug = getattr(ctx.market, 'slug', None)

        def decide(action, reason, legs=None, **feats):
            feats.setdefault('paper_mode', self.paper_mode)
            # Stated on EVERY row, skips included. Nothing downstream may pick
            # the vendor claim up off a log as if it were a measurement.
            feats.setdefault('claimed_gap_is_unverified_vendor_number', True)
            feats.setdefault('gap_never_measured_by_us', True)
            feats.setdefault('claimed_gap_range_f', (3.0, 8.0))
            feats.setdefault('gap_sign_is_not_constant', True)
            feats.setdefault('readings_fetched_inside_evaluate', True)
            feats.setdefault('resolution_source_read_from_rules_text', True)
            feats.setdefault('holds_to_resolution', True)
            return Decision(action=action, reason=reason,
                            strategy=self.strategy_name,
                            window_ts=ctx.window_ts, market_slug=slug,
                            legs=legs or [], features=feats)

        if ctx.market is None:
            return decide('SKIP', 'no_market')

        now = self.clock(ctx)
        if now is None:
            # Every gate below is timed: observation age and hours to resolution
            # both need a clock. Guessing one is guessing the freshness of the
            # only reading that matters.
            return decide('SKIP', 'no_clock')

        question = getattr(ctx.market, 'question', None)

        # 0. WRONG MARKET FAMILY. Checked FIRST, before the station, because a
        # global temperature market has no station either and would otherwise
        # be counted as `resolution_station_unknown` - pooling "we could not
        # read the station" with "this is not a city market at all", which are
        # two entirely different facts about two entirely different products.
        if is_global_temperature_market(question):
            return decide('SKIP', 'global_temperature_market_excluded',
                          question=question)

        # 1. THE RULES TEXT DECIDES. Not the table, and not the slug.
        station, station_status = resolution_station_checked(
            ctx.market, allow_fallback=self.allow_station_fallback)
        city_key = city_for_market(ctx.market)
        assumed = (WEATHER_MARKETS.get(city_key) or {}).get('icao')
        from_fallback = station_status == 'ok_from_city_fallback_table'
        feats: Dict[str, object] = {
            'city_key': city_key,
            'city_name': city_name_from_question(ctx.market),
            'market_metric': market_metric(question),
            'model_prices_point_in_time_not_daily_extreme': True,
            'assumed_station_from_table': assumed,
            'rules_station': None if from_fallback else station,
            'station_source': ('city_fallback_table' if from_fallback
                               else 'rules_text'),
            'station_is_a_fallback_guess': from_fallback,
            'allow_station_fallback': self.allow_station_fallback,
            'rules_text_present': bool(rules_text(ctx.market).strip()),
            'station_assumption_matches_rules': (
                None if (station is None or assumed is None)
                else station == str(assumed).upper()),
        }
        if station is None:
            # Two different causes, two different strings. Never pooled.
            return decide('SKIP', station_status, **feats)

        # 2. THE THRESHOLD. No default threshold and no default UNIT, ever.
        feats['question'] = question
        threshold, parse_status = parse_threshold_checked(question or '')
        feats['threshold_parse_status'] = parse_status
        if threshold is None:
            # `threshold_parse_status` keeps the five parse failures apart in a
            # log while the action-level reason says the one thing the gate is
            # about. The global-family case never reaches here; it was refused
            # at step 0 under its own reason.
            return decide('SKIP', 'threshold_unparseable', **feats)
        feats.update(threshold.to_dict())

        # 2b. THE LADDER EDGES ARE ONLY CORRECT IF THE SOURCE ROUNDS TO WHOLE
        # DEGREES. See `reporting_step_checked`. Applies to ladder rungs only:
        # a legacy prefix comparison carries no rounding step to disagree with.
        if threshold.is_ladder_rung:
            step, step_status = reporting_step_checked(ctx.market)
            feats['source_reporting_step_native'] = step
            feats['source_reporting_status'] = step_status
            if step is None:
                return decide('SKIP', 'source_reporting_precision_unknown',
                              **feats)
            if step < 1.0:
                return decide('SKIP', 'source_precision_finer_than_ladder_step',
                              **feats)

        # 2c. THE MODEL PRICES THE WRONG VARIABLE ON A DAILY-EXTREME MARKET.
        # This gate was added after the parser fix, because the parser fix is
        # what made the problem visible. MEASURED live on 2026-08-18 over 80
        # markets with real books and real METAR: 7 ENTERs, realised "edge"
        # 0.45 to 0.999. An 86-cent edge on a market quoting 0.1c is not edge,
        # it is the model being wrong, and it is wrong for a reason we can name
        # rather than a reason we have to go looking for.
        #
        #   Madrid, 11:00 UTC, station reading 33.0C. Market prices "the
        #   HIGHEST temperature today is 39C" at 0.70, because the afternoon
        #   peak has not happened yet. This model prices the reading at the
        #   settlement timestamp and says 0.000024. The market is right.
        #
        #   Buenos Aires, same minute, station reading 7.0C. Market prices
        #   "highest today is 8C or below" at 0.001, because the afternoon will
        #   be warmer. This model says 0.87. The market is right again, and
        #   note the error points the OTHER WAY, so a pooled win rate over both
        #   ladders would average the two biases into something that looks
        #   unbiased.
        #
        # Convention 17: a number that improves dramatically right after a step
        # that only removed a constraint is a baseline problem, not a result.
        # This is that shape exactly, so the refusal is the default and the
        # override is a named flag whose rows are separable.
        metric = feats.get('market_metric')
        feats['allow_daily_extreme_markets'] = self.allow_daily_extreme_markets
        if metric is not None and not self.allow_daily_extreme_markets:
            # Convention 11: a cannot-run, not a result. We are not saying
            # there is no edge in these markets. We are saying this model
            # cannot price them and will not pretend to.
            return decide('SKIP', 'daily_extreme_not_priced_by_point_in_time_model',
                          **feats)

        # 3. TIME TO RESOLUTION.
        hours = self.hours_to_resolution(ctx.market, now)
        feats['end_date'] = getattr(ctx.market, 'end_date', None)
        feats['hours_to_resolution'] = None if hours is None else round(hours, 3)
        if hours is None:
            return decide('SKIP', 'resolution_time_unknown', **feats)
        if hours <= 0:
            return decide('SKIP', 'market_past_resolution_time', **feats)
        if hours > self.max_hours_to_resolution:
            return decide('SKIP', 'resolution_too_far_out',
                          max_hours_to_resolution=self.max_hours_to_resolution,
                          **feats)

        # 4. THE AIRPORT READING - the one the market actually settles on.
        reading, read_status = self.airport_feed.observation(station)
        feats['airport_feed_status'] = read_status
        if reading is None:
            # An outage, a bad ICAO and an empty response all arrive here. Each
            # keeps its own status string in `airport_feed_status`; the
            # action-level reason says only "we have no reading", which is the
            # one fact the gate is about.
            return decide('SKIP', 'airport_reading_unavailable', **feats)

        age = reading.age_sec(now)
        feats.update({
            'airport_station': reading.station,
            'airport_temp_f': round(reading.temp_f, 2),
            'airport_temp_c': round(f_to_c(reading.temp_f), 2),
            'airport_obs_ts': reading.observed_ts,
            'airport_obs_age_sec': None if age is None else round(age, 1),
            'max_obs_age_sec': self.max_obs_age_sec,
        })
        if age is None:
            # An observation we cannot age is not a fresh observation.
            return decide('SKIP', 'airport_obs_time_missing', **feats)
        if age > self.max_obs_age_sec:
            # A front can move a station 15F in twenty minutes. An hour-old
            # reading taken during one is a different temperature, not a stale
            # copy of the same one.
            return decide('SKIP', 'airport_obs_stale', **feats)

        # 5. THE DOWNTOWN READING. Diagnostic. Never a gate.
        feats['fetch_downtown'] = self.fetch_downtown
        gap_f = None
        if self.fetch_downtown and city_key is not None:
            lat, lon = WEATHER_MARKETS[city_key]['downtown']
            downtown, downtown_status = self.downtown_feed.observation(lat, lon)
            feats['downtown_feed_status'] = downtown_status
            if downtown is not None:
                gap_f = reading.temp_f - downtown.temp_f
                feats['downtown_temp_f'] = round(downtown.temp_f, 2)
        else:
            feats['downtown_feed_status'] = (
                'not_fetched' if not self.fetch_downtown else 'no_city_mapping')
        feats['airport_minus_downtown_f'] = (None if gap_f is None
                                             else round(gap_f, 2))
        # A missing downtown reading blocks NOTHING. It costs us the
        # measurement, not the trade, and the row says which of the two happened
        # rather than leaving a silent None.
        feats['gap_measured_this_row'] = gap_f is not None
        feats['gap_within_claimed_range'] = (None if gap_f is None
                                             else 3.0 <= abs(gap_f) <= 8.0)

        # 6. OUR PROBABILITY.
        p_yes = self.probability_yes(reading.temp_f, threshold, hours)
        feats.update({
            'sigma_f': round(self.sigma_f(hours), 3),
            'sigma_model': 'sqrt_hours_diffusion_no_diurnal_term',
            'sigma_model_is_wrong_at_known_times_of_day': True,
            'model_p_yes': round(p_yes, 6),
            'model_p_no': round(1.0 - p_yes, 6),
            'confidence': round(max(p_yes, 1.0 - p_yes), 6),
            'confidence_is_model_output_not_measured_win_rate': True,
        })

        # 7. THE BOOKS.
        book_yes = ctx.book('Yes')
        book_no = ctx.book('No')
        if book_yes is None and book_no is None:
            return decide('SKIP', 'no_orderbook', **feats)
        ask_yes = None if book_yes is None else book_yes.best_ask
        ask_no = None if book_no is None else book_no.best_ask
        feats.update({'ask_yes': ask_yes, 'ask_no': ask_no})
        if ask_yes is None and ask_no is None:
            # An empty book and a bids-only book are the same fact for a BUY:
            # nothing to lift at any price.
            return decide('SKIP', 'no_asks', **feats)

        # The market's implied P(Yes). Read off the Yes ask where there is one,
        # otherwise inferred from the No ask. The inference is stated on the row,
        # not silent: an inferred probability carries the No side's spread as
        # well as its own.
        if ask_yes is not None:
            implied_yes = float(ask_yes)
            implied_source = 'yes_ask'
        else:
            implied_yes = 1.0 - float(ask_no)
            implied_source = 'inferred_from_no_ask'
        feats['market_implied_p_yes'] = round(implied_yes, 6)
        feats['market_implied_source'] = implied_source

        market_side = 'Yes' if implied_yes > 0.5 else 'No'
        model_side = 'Yes' if p_yes > 0.5 else 'No'
        feats.update({'market_implied_side': market_side,
                      'model_side': model_side})
        if abs(implied_yes - 0.5) < 1e-12 or abs(p_yes - 0.5) < 1e-12:
            # A market or a model sitting exactly on 0.5 has no direction to
            # disagree with. Its own cause, never pooled with a real agreement.
            return decide('SKIP', 'market_implied_direction_unreadable', **feats)
        if market_side == model_side:
            # The crowd already agrees with the airport. Whatever edge this
            # strategy claims comes from them anchoring on the WRONG
            # thermometer, so when they are anchored on the right one there is
            # nothing here. This is the strategy WORKING, and it is expected to
            # be the overwhelming majority of rows.
            return decide('SKIP', 'airport_agrees_with_market', **feats)

        side = model_side
        p_side = p_yes if side == 'Yes' else 1.0 - p_yes
        book = book_yes if side == 'Yes' else book_no
        feats.update({'outcome_side': side, 'model_p_side': round(p_side, 6)})
        if book is None or book.best_ask is None:
            # We disagree with the market on the one side we cannot buy.
            return decide('SKIP', 'no_asks', **feats)

        # 8. PRICE. The cap IS the edge: quote the worst price at which the
        # trade still clears MIN_EDGE, and gate on the BOOK-WALKED average under
        # it, so a fill several cents inside the cap is reported at what it
        # actually cost (the house rule in base.Leg.premium).
        cap = floor_to_tick(p_side - self.min_edge)
        feats.update({'min_edge': self.min_edge, 'entry_cap': cap,
                      'best_ask_on_side': book.best_ask})
        if cap < PRICE_TICK:
            # The threshold eats the whole price. There is no limit at or above
            # one tick that still carries the edge.
            return decide('SKIP', 'edge_below_min',
                          edge_reason='min_edge_exceeds_model_probability',
                          **feats)

        # Size DOWN to the notional cap rather than letting the adapter reject
        # the order. "20 shares does not fit in $10 at this price" and "the risk
        # gate refused you" are two different facts.
        affordable = int(math.floor(self.max_notional_usdc / cap + 1e-9))
        shares = min(self.target_shares, affordable)
        feats.update({'target_shares': self.target_shares,
                      'affordable_shares_at_cap': affordable,
                      'shares': shares,
                      'shares_capped_by_notional': shares < self.target_shares})
        if shares < self.min_shares:
            # Could not run, did not lose (convention 11).
            return decide('SKIP', 'unsizable_at_notional_cap', **feats)

        effective = effective_ask_for(book, shares, cap)
        feats['effective_ask'] = (None if effective is None
                                  else round(effective, 4))
        if effective is None:
            return decide('SKIP', 'unfillable_at_cap', **feats)
        if effective > cap:
            # INVARIANT GUARD. walk_book cannot return this under the same
            # limit, but the cap is the whole edge and a silent regression here
            # would be invisible. Same guard fair_value_arb keeps.
            return decide('SKIP', 'effective_ask_above_cap', **feats)

        edge = p_side - effective
        feats.update({
            'realized_edge': round(edge, 4),
            'realized_edge_bps': (round(edge / effective * 10_000, 1)
                                  if effective > 0 else None),
            'breakeven_win_rate_if_held': round(effective, 4),
            'notional_usdc': round(shares * effective, 4),
        })
        if edge < self.min_edge:
            # INVARIANT GUARD, and unreachable through the normal path: `cap` is
            # floored onto the tick grid at or below `p_side - min_edge`, so any
            # fill at or under the cap already clears MIN_EDGE. It stays because
            # the invariant is the whole entry rule, and a future edit to `cap`
            # that broke it would otherwise book entries with no edge and no
            # error. `edge_reason` keeps the two flavours apart in a log.
            return decide('SKIP', 'edge_below_min',
                          edge_reason='walked_price_ate_the_edge', **feats)

        return decide('ENTER', '',
                      legs=[Leg(outcome_side=side,
                                limit_price=cap,
                                order_type='taker',
                                shares=shares,
                                expected_price=effective)],
                      **feats)
