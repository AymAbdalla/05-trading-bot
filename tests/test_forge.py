"""The dedicated suite for `agents/forge.py`'s `validate()` gate.

WHY THIS FILE EXISTS SEPARATELY FROM THE OTHER THREE FORGE SUITES

`validate()` is the only thing standing between a candidate and a document in
`strategies/proposals/` that a later handoff will cite by number. It had no
suite of its own. What coverage existed was incidental:

  * `tests/test_forge_shadow_eval.py` (the "relaxed Forge schema" block) pins
    the four RETIRED refusals as warnings, and four of the eight surviving
    refusal categories: `unmeasurable_kill_condition`,
    `kill_condition_names_no_harness`, `below_min_edge_bps` and
    `unknowable_edge_claimed`.
  * `tests/test_forge_reasoner.py` pins that the model's candidates go through
    this same `validate()`, plus the proposal-numbering helpers.

The other four categories - `unknown_kind`, `missing_fields`,
`non_numeric_edge_estimate`, `non_finite_edge_estimate` - fired nowhere in the
test suite at all. This file covers all eight EXHAUSTIVELY rather than by
enumeration: `test_every_refusal_category_in_the_schema_is_reachable` asserts
that the categories this file triggers are exactly `forge.REFUSAL_CATEGORIES`,
so a ninth category cannot be added to the schema without a candidate that
reaches it. Convention 20 applied to the suite rather than only to the counters.

WHAT IS DELIBERATELY NOT DUPLICATED

The four categories already covered above are exercised here only inside the
exhaustiveness test and the refusal-ORDER tests, both of which assert something
the existing tests do not. Nothing here re-states a single-category assertion
that `test_forge_shadow_eval.py` already makes.

THE GAP THIS FILE PINS RATHER THAN FIXES

`related_graveyard_findings` is PRESENCE-checked and never RESOLVED
(`agents/forge.py:528`). A candidate that cites a strategy which has never
existed validates clean and silent; a candidate that honestly leaves the field
empty gets a warning. The asymmetry runs the wrong way and it is pinned here as
CURRENT BEHAVIOUR, deliberately not fixed - see
`test_a_fabricated_graveyard_link_is_accepted_and_an_honest_blank_is_warned`.

Policy NUMBERS (the 30bps and 20bps floors) live in the policy test in
`test_forge_shadow_eval.py`. Everything here derives its floor from
`forge.min_edge_bps_for()`, so a policy change does not break this file.
"""
import math
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agents import forge  # noqa: E402


def candidate(**over):
    """A candidate that validates clean, so every test varies ONE thing."""
    base = {
        'name': 'a_brand_new_idea',
        'kind': 'edge_hypothesis',
        'asset_class': 'PREDICTION_MARKET',
        'thesis': 'the close is mispriced in the last minutes of a window',
        'expected_edge_bps': 400,
        'kill_condition': ('net under 1c per share over 200 trades scored by '
                           'backtest/polymarket_harness.py'),
        'entry_exit_rules': 'buy under 40c, exit at resolution',
        'data_requirements': 'the live book',
        'related_graveyard_findings': 'none, the graveyard has no binaries',
        'body': 'the argument',
    }
    base.update(over)
    return base


def refusal_category(**over):
    """The category `validate()` refuses this candidate under."""
    with pytest.raises(forge.ProposalRefused) as exc:
        forge.validate(candidate(**over), [])
    return exc.value.category


def warning_categories(known=(), **over):
    """The warning categories `validate()` returns for this candidate."""
    return [w['category'] for w in forge.validate(candidate(**over),
                                                  list(known))]


# ---------------------------------------------------------------------------
# The clean baseline
# ---------------------------------------------------------------------------

def test_the_baseline_candidate_validates_clean_with_no_warnings():
    """Every other test in this file varies one field off this one.

    If the baseline ever starts warning, the single-field tests below stop
    isolating the field they claim to isolate, so this is a guard on the rest
    of the file as much as it is on `validate()`.
    """
    assert forge.validate(candidate(), []) == []


# ---------------------------------------------------------------------------
# Exhaustiveness: every category in the schema is reachable
# ---------------------------------------------------------------------------

#: One candidate override per refusal category. The KEYS of this mapping are
#: asserted equal to `forge.REFUSAL_CATEGORIES` below, so adding a category to
#: the schema without a candidate that reaches it fails here rather than
#: quietly shipping a refusal nobody has ever seen fire.
REACHES = {
    'unknown_kind': dict(kind='a_kind_that_does_not_exist'),
    'missing_fields': dict(thesis=''),
    'unmeasurable_kill_condition': dict(
        kill_condition='it stops working, per the harness'),
    'kill_condition_names_no_harness': dict(
        kill_condition='net edge below 30bps over 200 trades'),
    'non_numeric_edge_estimate': dict(expected_edge_bps='400'),
    'non_finite_edge_estimate': dict(expected_edge_bps=float('inf')),
    'below_min_edge_bps': dict(expected_edge_bps=0),
    'unknowable_edge_claimed': dict(kind='governance',
                                    expected_edge_bps=400),
}


@pytest.mark.parametrize('category', sorted(REACHES))
def test_each_refusal_category_is_reached_by_its_own_candidate(category):
    assert refusal_category(**REACHES[category]) == category


def test_every_refusal_category_in_the_schema_is_reachable():
    """Convention 20, turned on the suite itself.

    A refusal category that no test can reach is indistinguishable from one
    that no longer fires. This is the assertion that makes the file EXHAUSTIVE
    rather than a list of eight tests somebody has to remember to extend.
    """
    assert set(REACHES) == set(forge.REFUSAL_CATEGORIES)


def test_a_refused_candidate_carries_its_category_and_detail():
    """`ProposalRefused` is the transport for the run log's category counts.

    `generate()` reads `.category` and `.detail` off the exception; a refusal
    that only carried a message would land in the run log as an uncategorised
    skip, which convention 20 counts as a missing number.
    """
    with pytest.raises(forge.ProposalRefused) as exc:
        forge.validate(candidate(thesis='', entry_exit_rules=''), [])
    assert exc.value.category == 'missing_fields'
    # Every missing field is named, not just the first one found.
    assert set(exc.value.detail.split(',')) == set(
        ['thesis', 'entry_exit_rules'])
    assert 'missing_fields' in str(exc.value)


# ---------------------------------------------------------------------------
# missing_fields: presence, emptiness, and the one nullable exception
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('field', forge.REQUIRED_FIELDS)
def test_every_required_field_is_refused_when_absent(field):
    """Absent, not merely empty. `expected_edge_bps` is nullable in VALUE and
    still mandatory in PRESENCE: `None` is a recorded unknown, a missing key is
    a candidate that never thought about it."""
    cand = candidate()
    cand.pop(field)
    with pytest.raises(forge.ProposalRefused) as exc:
        forge.validate(cand, [])
    assert exc.value.category == 'missing_fields'
    assert field in exc.value.detail


@pytest.mark.parametrize(
    'field', [f for f in forge.REQUIRED_FIELDS
              if f not in forge.NULLABLE_FIELDS])
def test_every_non_nullable_required_field_is_refused_when_empty(field):
    cand = candidate()
    cand[field] = ''
    with pytest.raises(forge.ProposalRefused) as exc:
        forge.validate(cand, [])
    assert exc.value.category == 'missing_fields'
    assert field in exc.value.detail


def test_a_nullable_field_that_is_present_and_falsy_is_not_missing():
    """`expected_edge_bps` is the whole reason NULLABLE_FIELDS exists.

    A repair records `None` (convention 11: unknown is not zero) and must NOT
    be refused as a missing field for doing so. It gets past `missing_fields`
    and is then judged on its KIND, which is the correct place for it.
    """
    assert forge.validate(
        candidate(kind='repair', expected_edge_bps=None), []) == []
    # On an edge_hypothesis the same null falls through to the edge check
    # rather than being caught as missing.
    assert refusal_category(expected_edge_bps=None) == \
        'non_numeric_edge_estimate'


def test_the_optional_fields_are_never_required():
    cand = candidate()
    for field in forge.OPTIONAL_FIELDS:
        cand.pop(field, None)
    assert set(forge.OPTIONAL_FIELDS).isdisjoint(forge.REQUIRED_FIELDS)
    # Only the documented warning, never a refusal.
    assert [w['category'] for w in forge.validate(cand, [])] == \
        ['no_graveyard_link_warning']


# ---------------------------------------------------------------------------
# The edge estimate
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('bad', ['400', None, [], dict(), object()])
def test_a_non_numeric_edge_estimate_is_refused(bad):
    assert refusal_category(expected_edge_bps=bad) == \
        'non_numeric_edge_estimate'


def test_a_bool_is_not_a_number_here():
    """`isinstance(True, int)` is True in Python, so the bool guard is load
    bearing rather than defensive. Without it `expected_edge_bps: true` would
    read as 1bps and be refused for being small, which reports the wrong
    problem: the value is not an estimate at all."""
    assert refusal_category(expected_edge_bps=True) == \
        'non_numeric_edge_estimate'
    assert refusal_category(expected_edge_bps=False) == \
        'non_numeric_edge_estimate'


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), float('-inf')])
def test_a_non_finite_edge_estimate_is_refused(bad):
    """Convention 19. A non-finite serialises into a file that `json.loads`
    accepts and every other parser rejects, so it is stopped here, at write
    time, rather than downstream in whatever reads the proposal."""
    assert not math.isfinite(bad)
    assert refusal_category(expected_edge_bps=bad) == \
        'non_finite_edge_estimate'


def test_the_finiteness_check_runs_before_the_floor_check():
    """Order is the whole point of this one.

    `-inf` is below every floor and `+inf` is above every floor. If the floor
    comparison ran first they would be refused under two DIFFERENT categories
    for the same defect, and the run log would carry a `below_min_edge_bps`
    count that included a value which is not a number on the number line.
    """
    assert refusal_category(expected_edge_bps=float('-inf')) == \
        'non_finite_edge_estimate'
    assert refusal_category(expected_edge_bps=float('inf')) == \
        'non_finite_edge_estimate'


def test_the_floor_is_taken_from_the_candidates_own_asset_class():
    """Derived, not hardcoded. The floor NUMBERS are policy and are pinned in
    `test_forge_shadow_eval.py::test_edge_floor_is_instrument_aware`. What is
    pinned here is that `validate()` consults `min_edge_bps_for(asset_class)`
    rather than one global constant, which is what a second asset class with a
    different tick would break."""
    for cls in ('PREDICTION_MARKET', 'CRYPTO'):
        floor = forge.min_edge_bps_for(cls)
        assert forge.validate(
            candidate(asset_class=cls, expected_edge_bps=floor), []) == []
        assert refusal_category(asset_class=cls,
                                expected_edge_bps=floor - 1) == \
            'below_min_edge_bps'


def test_an_unlisted_asset_class_falls_back_to_the_default_floor():
    """An unlisted class only WARNS, so it still reaches the floor check and
    must be judged against something. `min_edge_bps_for` returns the default
    for anything it does not know, including `None`."""
    assert forge.min_edge_bps_for('WEATHER') == forge.MIN_GROSS_EDGE_BPS
    assert forge.min_edge_bps_for(None) == forge.MIN_GROSS_EDGE_BPS
    cats = warning_categories(asset_class='WEATHER',
                              expected_edge_bps=forge.MIN_GROSS_EDGE_BPS)
    assert 'unlisted_asset_class_warning' in cats


@pytest.mark.parametrize('kind', forge.NULL_EDGE_KINDS)
def test_every_null_edge_kind_refuses_a_number_and_accepts_none(kind):
    """Exhaustive over the tuple, not over one example.

    `test_forge_shadow_eval.py` pins `experiment`. This pins that the rule is a
    property of NULL_EDGE_KINDS itself, so adding a kind to that tuple cannot
    leave it silently able to claim an edge it cannot know.
    """
    assert refusal_category(kind=kind, expected_edge_bps=400) == \
        'unknowable_edge_claimed'
    assert forge.validate(
        candidate(kind=kind, expected_edge_bps=None), []) == []


@pytest.mark.parametrize(
    'kind', [k for k in forge.KINDS if k not in forge.NULL_EDGE_KINDS])
def test_every_edge_claiming_kind_must_put_a_number_on_it(kind):
    assert refusal_category(kind=kind, expected_edge_bps=None) == \
        'non_numeric_edge_estimate'
    assert forge.validate(candidate(kind=kind), []) == []


# ---------------------------------------------------------------------------
# The kill condition (convention 6, the one hard content constraint left)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('scorer', forge.KNOWN_SCORERS)
def test_every_known_scorer_satisfies_the_named_harness_rule(scorer):
    """KNOWN_SCORERS is a contract with the proposal authors: a kill condition
    naming any scorer on that list must pass. An entry that stopped matching
    would refuse proposals that are correctly written, which reads at the other
    end as an author error."""
    assert forge.validate(
        candidate(kill_condition='under 30bps over 200 trades in %s' % scorer),
        []) == []


def test_the_harness_match_is_case_insensitive_and_a_substring():
    """Documented behaviour, pinned so the looseness is visible.

    Case-insensitive substring is deliberate: it lets "the vectorized harness"
    and "backtest/polymarket_harness.py" both pass off one entry. The cost is
    that the match cannot tell a scorer from a word that merely contains one,
    which is a known false-ACCEPT rather than a false-refusal, so it errs
    toward letting a proposal through rather than blocking a correct one.
    """
    assert forge.validate(
        candidate(kill_condition='below 30bps in the HARNESS'), []) == []
    assert forge.validate(
        candidate(kill_condition='below 30bps, see JUDGE.PY'), []) == []


@pytest.mark.parametrize('kill', [
    'net edge goes negative, measured by the vectorized harness',
    'the strategy stops working per judge.py',
    'no edge remains in run_go_nogo',
])
def test_a_kill_condition_with_no_digit_anywhere_is_unmeasurable(kill):
    """A threshold, not a mood. Each of these NAMES a harness and still has no
    number, so the two halves of convention 6 are independent."""
    assert not any(ch.isdigit() for ch in kill)
    assert refusal_category(kill_condition=kill) == \
        'unmeasurable_kill_condition'


def test_the_number_may_come_from_anywhere_in_the_kill_condition():
    """The check is `any(ch.isdigit())`, so a date satisfies it as readily as a
    threshold does. Pinned as current behaviour: this is a weak test for
    "states a number", and a proposal can pass it without stating a threshold
    at all."""
    assert forge.validate(
        candidate(kill_condition='re-read in the harness on 2026-09-01'),
        []) == []


# ---------------------------------------------------------------------------
# Refusal ORDER. `validate()` raises on the FIRST failure, so the category a
# multiply-broken candidate lands in is a property of the check order.
# ---------------------------------------------------------------------------

def test_an_unknown_kind_is_refused_before_the_missing_fields_are_counted():
    """Correct order: with an unrecognised kind we do not know which fields are
    even required, so reporting the missing ones would be reporting against a
    schema that does not apply."""
    cand = candidate(kind='a_kind_that_does_not_exist', thesis='',
                     entry_exit_rules='')
    with pytest.raises(forge.ProposalRefused) as exc:
        forge.validate(cand, [])
    assert exc.value.category == 'unknown_kind'


def test_missing_fields_are_refused_before_the_edge_is_examined():
    """`validate()` indexes `candidate['expected_edge_bps']` straight after the
    presence check. If the order inverted, a candidate missing that key would
    raise KeyError - an uncategorised crash - instead of a counted refusal."""
    cand = candidate(expected_edge_bps='not a number')
    cand.pop('kill_condition')
    with pytest.raises(forge.ProposalRefused) as exc:
        forge.validate(cand, [])
    assert exc.value.category == 'missing_fields'


def test_the_edge_is_judged_before_the_kill_condition():
    """A candidate that is both under the floor and unmeasurable is counted
    under the edge, and only the edge. Pinned because the run log's
    per-category counts get read as "how many proposals failed THIS way", and a
    candidate can only be in one bucket."""
    floor = forge.min_edge_bps_for('PREDICTION_MARKET')
    assert refusal_category(expected_edge_bps=floor - 1,
                            kill_condition='it stops working') == \
        'below_min_edge_bps'


def test_warnings_are_collected_across_a_candidate_not_short_circuited():
    """A refusal stops at the first failure; a WARNING must not. Three retired
    categories fire on one candidate here and all three must be reported, or
    the downgrade from refusal to warning would have lost information that
    convention 20 says must survive it."""
    cats = warning_categories(
        known=['a_brand_new_idea'],
        asset_class='MULTI',
        related_graveyard_findings='')
    assert set(cats) == set(['duplicate_name_warning', 'multi_class_warning',
                             'no_graveyard_link_warning'])
    # MULTI warns on an edge_hypothesis; the fourth retired category needs a
    # class outside the vocabulary entirely.
    assert 'unlisted_asset_class_warning' in warning_categories(
        asset_class='WEATHER', expected_edge_bps=forge.MIN_GROSS_EDGE_BPS)
    assert set(forge.WARNING_CATEGORIES) == \
        set(forge.RETIRED_REFUSAL_CATEGORIES.values())


def test_no_warning_category_is_also_a_refusal_category():
    """The retirement was a MOVE, not a copy. A category on both lists would
    make `generate()`'s two counters double-count the same candidate."""
    assert set(forge.WARNING_CATEGORIES).isdisjoint(forge.REFUSAL_CATEGORIES)
    assert set(forge.RETIRED_REFUSAL_CATEGORIES).isdisjoint(
        forge.REFUSAL_CATEGORIES)


# ---------------------------------------------------------------------------
# THE GAP: `related_graveyard_findings` is presence-checked, never resolved.
# agents/forge.py:528. Pinned as current behaviour, NOT fixed here.
# ---------------------------------------------------------------------------

def test_a_fabricated_graveyard_link_is_accepted_and_an_honest_blank_is_warned():
    """The asymmetry runs the wrong way, and this test says so out loud.

    `validate()` asks only whether the field is truthy. It never checks the
    cited finding against `known_strategies`, against
    `research/graveyard/summary.json`, or against anything else. So:

      * a candidate citing a strategy that HAS NEVER EXISTED passes silently;
      * a candidate that honestly leaves the field blank gets a warning.

    The cheapest thing a proposal can do to clear the check is invent a link,
    and an invented link in a proposal becomes a cited fact two documents
    later, which is the exact failure convention 11 exists to prevent.

    This is pinned rather than fixed because resolving the link is a PRODUCTION
    change to `agents/forge.py` and is scoped to the S7 follow-up. When that
    lands, this is the test that must be rewritten, and its rewrite is the
    record that the behaviour changed on purpose.
    """
    known = ['rsi_extreme']
    fiction = 'the_strategy_that_was_never_swept: buried for reasons'
    assert not any(k in fiction for k in known)
    # Cited fiction: clean, silent, indistinguishable from a real link.
    assert forge.validate(
        candidate(related_graveyard_findings=fiction), known) == []
    # Honest blank: warned.
    assert 'no_graveyard_link_warning' in [
        w['category'] for w in forge.validate(
            candidate(related_graveyard_findings=''), known)]


def test_known_strategies_is_consulted_for_the_name_and_never_for_the_link():
    """The graveyard list Forge already holds is right there in the signature.

    `validate(candidate, known_strategies)` uses that list for exactly one
    thing: the duplicate-NAME warning. The same list could resolve
    `related_graveyard_findings` and does not. Pinning both halves against one
    list makes the asymmetry a test failure rather than a code-reading
    exercise.
    """
    known = ['rsi_extreme', 'vol_breakout']
    # The NAME is checked against the list.
    assert warning_categories(known=known, name='rsi_extreme') == \
        ['duplicate_name_warning']
    # The LINK is not, in either direction: naming a real graveyard strategy
    # earns nothing, and naming nothing real costs nothing.
    assert forge.validate(
        candidate(related_graveyard_findings='rsi_extreme died of costs'),
        known) == []
    assert forge.validate(
        candidate(related_graveyard_findings='xyzzy_not_a_strategy'),
        known) == []


@pytest.mark.parametrize('truthy', [
    'x', '0', 'none', 'n/a', ['anything'], dict(a=1), 42])
def test_any_truthy_value_clears_the_graveyard_link_check(truthy):
    """Not even a string is required. The check is `if not candidate.get(...)`,
    so a bare `42` satisfies a field the schema describes as prose about what
    is already buried in this family."""
    assert forge.validate(
        candidate(related_graveyard_findings=truthy), []) == []


# ---------------------------------------------------------------------------
# What `validate()` does NOT touch: `status`.
#
# `validate()` never reads or writes `status`, and `render()` defaults it to
# PROPOSED on every write. Because `write_proposal()` overwrites in place, a
# proposal promoted by hand is reset by the next Forge run. This is the
# mechanism behind "every proposal on disk reads PROPOSED".
# ---------------------------------------------------------------------------

def test_status_is_not_part_of_the_validation_contract():
    """Any status validates, including one outside the lifecycle.

    `strategies/proposals/README.md` calls `status` "the single source of truth
    for where a proposal is" and names four values. `validate()` enforces none
    of that vocabulary, so the field is documentation rather than a checked
    contract.
    """
    for status in ('PROPOSED', 'ACCEPTED', 'BUILT', 'REJECTED', 'BANANA'):
        assert forge.validate(candidate(status=status), []) == []


def test_render_defaults_status_to_proposed_and_echoes_one_that_is_supplied():
    assert 'status: PROPOSED' in forge.render(candidate())
    assert 'status: BUILT' in forge.render(candidate(status='BUILT'))


def test_rewriting_a_proposal_resets_a_hand_promoted_status(tmp_path,
                                                            monkeypatch):
    """The clobber, demonstrated end to end rather than argued.

    `write_proposal()` opens the path in 'w' and `generate()` keys the number
    on the SLUG, so re-running Forge over a candidate list that still contains
    a proposal REWRITES that proposal's file. The candidate dicts in
    `agents/forge_candidates.py` carry no `status`, so `render()` fills in
    PROPOSED and any hand promotion is gone.

    Nothing errors and nothing is logged as changed, which is why seventeen
    Forge runs left every proposal on disk reading PROPOSED with no sign that a
    status had ever been set.
    """
    monkeypatch.setattr(forge, 'PROPOSALS_DIR', str(tmp_path))
    cand = candidate()
    path = tmp_path / os.path.basename(forge.write_proposal(cand, 7, []))
    assert path.name == '007-a-brand-new-idea.md'
    assert 'status: PROPOSED' in path.read_text()

    # A human promotes it, the way the README lifecycle says to.
    path.write_text(path.read_text().replace('status: PROPOSED',
                                             'status: BUILT'))
    assert 'status: BUILT' in path.read_text()

    # Forge runs again over the same candidate. Same slug, same number, and
    # the promotion is silently gone.
    record = forge.generate([cand], dict(known_strategies=[]))
    assert record['written'][0]['number'] == 7
    assert record['refused'] == []
    assert 'status: BUILT' not in path.read_text()
    assert 'status: PROPOSED' in path.read_text()
