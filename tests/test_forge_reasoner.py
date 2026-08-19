"""Tests for the Opus reasoning layer in front of Forge.

Three things are being pinned here.

1. The BRIEF must actually contain the evidence it claims to. A prompt that
   says "here is the vault" and then ships an empty section is convention 22
   territory: a claim in a docstring is not a wiring test. So the tests build a
   real temp vault and a real temp hypothesis graph and then look for those
   exact strings in the generated prompt.

2. Convention 11 must survive the round trip. A model turn that COULD NOT RUN
   is NOT_TESTED. A model turn that ran and said something unparseable is a
   RESULT, and a different one. A model turn that ran and proposed nothing is a
   third. All three fall back to the deterministic candidate list, and the run
   log has to say which happened.

3. PYTHON still holds the pen. A candidate the model returns goes through the
   same `forge.validate()` as a hand written one, so a kill condition with no
   number is refused with the same category and lands in the same ledger.

No test here may spawn a real Opus turn. `AYM_LLM_DRY_RUN=1` is forced for the
whole module and every test that cares about the reply monkeypatches
`llm_client.run_task` on top of that, so a missed patch degrades to the canned
dry run reply rather than to a real spend.
"""
import json
import os
import re
import sqlite3
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agents import forge  # noqa: E402
from agents import forge_reasoner as fr  # noqa: E402
from agents import hypothesis_graph as hg  # noqa: E402
from agents import llm_client  # noqa: E402
from agents import vault_digest  # noqa: E402
from agents import vault_reader  # noqa: E402


@pytest.fixture(autouse=True)
def _never_spawn_a_real_turn(monkeypatch):
    """Belt and braces: no test in this file may reach a real `claude -p`."""
    monkeypatch.setenv(llm_client.DRY_RUN_ENV, '1')


# ---------------------------------------------------------------------------
# Fixtures: a real temp vault and a real temp hypothesis graph
# ---------------------------------------------------------------------------

LESSON_TEXT = """# Lesson: the strike proxy floor is a gate, not a dial

**Status:** ACTIVE
**Strategy:** PM_corridor_collector
**Failure Mode:** spread_eats_edge

A bigger noise floor rejects more windows, never fewer.
"""


@pytest.fixture
def temp_vault(tmp_path, monkeypatch):
    """A four section vault on disk, wired into `vault_reader` by monkeypatch."""
    root = tmp_path / 'vault' / 'Trading'
    lessons = root / 'Lessons'
    blowups = root / 'Blowup-Reports'
    cards = root / 'Strategy-Cards'
    cycles = root / 'Forge-Cycle-Summaries'
    for path in (lessons, blowups, cards, cycles):
        path.mkdir(parents=True)
    (lessons / 'strike-proxy-floor-direction.md').write_text(LESSON_TEXT)
    (cards / 'fair-value-arb-card.md').write_text(
        '# Strategy Card: fair value arb\n\n**Failure Mode:** stop_too_tight\n')

    monkeypatch.setattr(vault_reader, 'VAULT_ROOT', str(root))
    monkeypatch.setattr(vault_reader, 'SECTIONS', (
        ('lessons', str(lessons)),
        ('blowup_reports', str(blowups)),
        ('strategy_cards', str(cards)),
        ('cycle_summaries', str(cycles)),
    ))
    return root


@pytest.fixture
def temp_graph(tmp_path):
    """A DB carrying `hypothesis_graph` and nothing else.

    Deliberately missing `signals` / `positions` / `equity_snapshots`, so
    `forge_shadow_eval.evaluate()` reports it unreadable rather than empty.
    That is the convention 11 shape we want exercised, and it keeps the test
    off the real 193 MB trading DB.
    """
    path = str(tmp_path / 'graph.db')
    conn = hg.connect(path)
    hg.add_hypothesis(
        conn,
        strategy_name='V2_vwap_magnet',
        hypothesis='price reverts to session VWAP after a 2 sigma excursion',
        status='TESTED_FAILED',
        source='graveyard',
        asset_class='CRYPTO',
        failure_mode='entry_signal_wrong',
        notes='gross PF > 1 on only 31.0% of priced rows.',
    )
    hg.add_hypothesis(
        conn,
        strategy_name='PM_fair_value_arb',
        hypothesis='the CLOB reprices more slowly than spot inside a 5m window',
        status='TESTED_FAILED',
        source='shadow',
        asset_class='PREDICTION_MARKET',
        failure_mode='stop_too_tight',
        notes='every entry stopped out before the window resolved.',
    )
    hg.add_hypothesis(
        conn,
        strategy_name='PM_weather_arb',
        hypothesis='airport METAR leads the resolution source',
        status='UNTESTED',
        source='proposal',
        asset_class='PREDICTION_MARKET',
        notes='never evaluated its own condition. Convention 11.',
    )
    conn.close()
    return path


MINIMAL_GAPS = {
    'evidence_errors': {},
    'known_strategies': ['V2_vwap_magnet'],
    'non_firing': [],
    'asset_classes': {'covered': {}, 'absent': ['PREDICTION_MARKET']},
    'worst_pooled': [],
    'failed_assertions': [],
}


def _brief(temp_graph, **kwargs):
    return fr.gather_evidence(db_path=temp_graph, gaps=dict(MINIMAL_GAPS),
                              include_shadow=False, **kwargs)


def _good_candidate(name='pm_probe', **overrides):
    """A candidate shaped exactly as the prompt asks the model to shape one."""
    candidate = {
        'name': name,
        'kind': 'edge_hypothesis',
        'asset_class': 'PREDICTION_MARKET',
        'thesis': 'resting size at window open reprices slowly on a reversal.',
        'expected_edge_bps': 240,
        'kill_condition': (
            'if net pnl per resolved position is below 0 cents over 200 or '
            'more positions in backtest/polymarket_harness.py, retire it'),
        'entry_exit_rules': 'enter at 0.42, stop at 0.00, exit at resolution.',
        'data_requirements': 'the CLOB book and BTC spot. We have both.',
        'why_it_might_fail': 'the slow repricing may be a fee, not an edge.',
        'addresses_past_failure': (
            'hypothesis_graph id 2, PM_fair_value_arb, failure_mode '
            'stop_too_tight, plus the vault note '
            'strike-proxy-floor-direction.md.'),
        'body': '## Arithmetic\n\n1c on a 42c contract is 238bps.\n',
    }
    candidate.update(overrides)
    return candidate


def _reply(candidates):
    return '```json\n' + json.dumps(candidates) + '\n```'


def _fake_turn(text, ok=True, error=None):
    def run_task(task, prompt, **kwargs):
        return llm_client.LLMResult(text, 'opus', task, 0.1, ok=ok,
                                    error=error)
    return run_task


# ---------------------------------------------------------------------------
# The brief and the prompt
# ---------------------------------------------------------------------------

def test_the_prompt_carries_the_vault_and_the_failed_hypotheses(
        temp_vault, temp_graph):
    """Convention 22: the prompt must contain the evidence, not a claim to it."""
    prompt = fr.build_prompt(_brief(temp_graph), n_proposals=3)

    # The vault, by note filename and by body text.
    assert 'strike-proxy-floor-direction.md' in prompt
    assert 'A bigger noise floor rejects more windows' in prompt
    assert 'fair-value-arb-card.md' in prompt

    # The hypothesis graph, by id AND by strategy name, because the prompt
    # tells the model to cite both.
    assert 'V2_vwap_magnet' in prompt
    assert 'PM_fair_value_arb' in prompt
    assert 'entry_signal_wrong' in prompt
    assert '**id 1**' in prompt and '**id 2**' in prompt

    # UNTESTED rows are NOT in the failed block. Convention 11: a strategy that
    # could not run has not failed, and offering it as a failure would invite
    # the model to "address" a failure that never happened.
    failed_block = prompt.split('# HYPOTHESIS GRAPH')[1].split(
        '# OBSIDIAN VAULT')[0]
    assert 'PM_weather_arb' not in failed_block


def test_the_prompt_states_the_schema_and_the_instrument_edge_floors(
        temp_vault, temp_graph):
    """The schema is generated FROM forge's constants, not typed out twice."""
    prompt = fr.build_prompt(_brief(temp_graph))

    for field in forge.REQUIRED_FIELDS:
        assert '"%s"' % field in prompt
    for field in fr.REASONER_FIELDS:
        assert '"%s"' % field in prompt

    # Convention 5, instrument aware. Both floors must be visible or the model
    # wastes a proposal under one of them.
    assert re.search(r'PREDICTION_MARKET\s+20 bps', prompt)
    assert re.search(r'CRYPTO\s+30 bps', prompt)
    assert re.search(r'SPORTS\s+20 bps', prompt)
    assert str(forge.MIN_GROSS_EDGE_BPS) in prompt

    # Convention 6: the model has to know which scorer names are recognised.
    for scorer in forge.KNOWN_SCORERS:
        assert scorer in prompt

    # Convention 11: repair and experiment must record a null edge.
    for kind in forge.NULL_EDGE_KINDS:
        assert kind in prompt


def test_the_prompt_template_has_no_em_dashes_or_double_hyphens():
    """Aym's style rule, applied to the text this repo actually authors.

    Only the template and the field help are checked. The brief embeds evidence
    written elsewhere, and rewriting somebody else's note to satisfy a style
    rule would be falsifying the record.
    """
    authored = fr._PROMPT_TEMPLATE + ''.join(fr._FIELD_HELP.values())
    assert '—' not in authored
    assert '--' not in authored


def test_truncation_is_reported_in_the_brief_and_in_the_prompt(
        temp_vault, temp_graph, monkeypatch):
    """Convention 20: a silent truncation is a missing number."""
    monkeypatch.setattr(fr, 'HYPOTHESIS_CAP_CHARS', 200)
    brief = _brief(temp_graph)
    prompt = fr.build_prompt(brief)

    assert brief['truncations'], 'a 200 char cap must have cut something'
    cut = [t for t in brief['truncations']
           if t['what'] == 'the hypothesis graph']
    assert len(cut) == 1
    assert cut[0]['dropped_chars'] > 0
    assert cut[0]['kept_chars'] + cut[0]['dropped_chars'] == cut[0][
        'total_chars']

    # And the model is told, in the prompt itself.
    assert 'TRUNCATED' in prompt
    assert 'WHAT THIS BRIEF DROPPED' in prompt


def test_rendering_the_same_brief_twice_does_not_double_count_truncations(
        temp_vault, temp_graph, monkeypatch):
    monkeypatch.setattr(fr, 'HYPOTHESIS_CAP_CHARS', 200)
    brief = _brief(temp_graph)
    fr.render_brief(brief)
    first = list(brief['truncations'])
    fr.render_brief(brief)
    assert brief['truncations'] == first


def test_an_unreadable_hypothesis_db_is_not_an_empty_one(tmp_path, temp_vault):
    """Convention 11 at the evidence layer."""
    missing = str(tmp_path / 'there-is-no-db-here.db')
    block = fr.load_failed_hypotheses(missing)
    assert block['status'] == 'unreadable'
    assert block['error']
    assert block['shown'] == []

    rendered = fr.render_hypothesis_graph(block)
    assert 'UNREADABLE' in rendered
    assert 'NOT_TESTED' in rendered
    assert 'no failures on record' in rendered


# ---------------------------------------------------------------------------
# Reading the reply
# ---------------------------------------------------------------------------

def test_a_reply_with_nan_lands_in_its_own_category(temp_vault, temp_graph,
                                                    monkeypatch):
    """Convention 19, and convention 20's "two causes, two counters".

    `json.loads` accepts NaN. `llm_client.strict_json_loads` does not, and this
    test also pins the discrimination `parse_candidates` makes between "not
    JSON" and "JSON carrying a number the model did not have".
    """
    reply = ('[{"name": "x", "expected_edge_bps": NaN, '
             '"why_it_might_fail": "a", "addresses_past_failure": "b"}]')
    monkeypatch.setattr(llm_client, 'run_task', _fake_turn(reply))

    outcome = fr.reason(_brief(temp_graph))
    assert outcome.status == 'unusable_reply'
    assert outcome.not_tested is False, 'the turn RAN. It is not NOT_TESTED.'
    counts = outcome.dropped_by_category()
    assert counts['reply_contained_non_finite_number'] == 1
    assert counts['reply_not_parseable_json'] == 0
    assert outcome.candidates == []


def test_prose_instead_of_json_is_a_result_not_not_tested(
        temp_vault, temp_graph, monkeypatch):
    monkeypatch.setattr(
        llm_client, 'run_task',
        _fake_turn('I had a think and I would rather not propose anything.'))
    outcome = fr.reason(_brief(temp_graph))
    assert outcome.status == 'unusable_reply'
    assert outcome.not_tested is False
    counts = outcome.dropped_by_category()
    assert counts['reply_not_parseable_json'] == 1
    assert counts['reply_contained_non_finite_number'] == 0


def test_a_json_object_instead_of_an_array_has_its_own_category(
        temp_vault, temp_graph, monkeypatch):
    monkeypatch.setattr(llm_client, 'run_task',
                        _fake_turn(json.dumps(_good_candidate())))
    outcome = fr.reason(_brief(temp_graph))
    assert outcome.status == 'unusable_reply'
    assert outcome.dropped_by_category()['reply_not_a_list'] == 1


def test_a_dead_model_is_not_tested_and_does_not_crash(temp_vault, temp_graph,
                                                       monkeypatch):
    """Convention 11's ONE case: the turn could not run."""
    monkeypatch.setattr(
        llm_client, 'run_task',
        _fake_turn('', ok=False, error='claude is not on PATH'))

    outcome = fr.reason(_brief(temp_graph))
    assert outcome.status == 'NOT_TESTED'
    assert outcome.not_tested is True
    assert outcome.ok is False
    assert outcome.candidates == []
    assert outcome.dropped_by_category()['llm_turn_could_not_run'] == 1
    assert 'not on PATH' in outcome.error
    # The LLMResult survives into the record so the run log can say what broke.
    assert outcome.to_dict()['llm']['ok'] is False


def test_an_empty_array_is_a_result_not_a_failure(temp_vault, temp_graph,
                                                  monkeypatch):
    """The model ran, read the evidence and declined. That is a measurement."""
    monkeypatch.setattr(llm_client, 'run_task', _fake_turn('[]'))
    outcome = fr.reason(_brief(temp_graph))
    assert outcome.status == 'no_candidates'
    assert outcome.not_tested is False
    assert sum(outcome.dropped_by_category().values()) == 0


def test_a_candidate_missing_its_reasoning_is_dropped_under_its_own_name(
        temp_vault, temp_graph, monkeypatch):
    """`validate()` cannot police these two, so the reasoner does."""
    no_failure_mode = _good_candidate('a', why_it_might_fail='   ')
    no_citation = _good_candidate('b', addresses_past_failure='')
    monkeypatch.setattr(llm_client, 'run_task', _fake_turn(
        _reply([no_failure_mode, no_citation, _good_candidate('c')])))

    outcome = fr.reason(_brief(temp_graph))
    assert outcome.status == 'ok'
    assert [c['name'] for c in outcome.candidates] == ['c']
    counts = outcome.dropped_by_category()
    assert counts['missing_why_it_might_fail'] == 1
    assert counts['missing_addresses_past_failure'] == 1


def test_the_drop_category_schema_is_complete_and_reported_at_zero(
        temp_vault, temp_graph, monkeypatch):
    """Convention 20: a category that fired zero times is still a measurement."""
    monkeypatch.setattr(llm_client, 'run_task',
                        _fake_turn(_reply([_good_candidate()])))
    counts = fr.reason(_brief(temp_graph)).dropped_by_category()
    assert set(counts) == set(fr.DROP_CATEGORIES)
    assert sum(counts.values()) == 0


def test_the_reasoner_accounting_identity_holds(temp_vault, temp_graph,
                                                monkeypatch):
    """entries in the reply == kept + per entry drops. Asserted, not assumed."""
    payload = [_good_candidate('a'), 'not an object',
               _good_candidate('b', why_it_might_fail=''),
               _good_candidate('c')]
    monkeypatch.setattr(llm_client, 'run_task', _fake_turn(_reply(payload)))

    outcome = fr.reason(_brief(temp_graph))
    per_entry = [d for d in outcome.dropped
                 if d['category'] in fr.ENTRY_DROP_CATEGORIES]
    assert outcome.entries_in_reply == 4
    assert outcome.entries_in_reply == len(outcome.candidates) + len(per_entry)
    assert outcome.dropped_by_category()['entry_not_an_object'] == 1


def test_kept_candidates_carry_their_reasoning_into_the_document(
        temp_vault, temp_graph, monkeypatch):
    """`forge.render()` writes the body, so the reasoning must live there.

    `why_it_might_fail` and `addresses_past_failure` are in neither
    REQUIRED_FIELDS nor OPTIONAL_FIELDS, so without the fold they would be
    validated and then thrown away.
    """
    monkeypatch.setattr(llm_client, 'run_task',
                        _fake_turn(_reply([_good_candidate()])))
    candidate = fr.reason(_brief(temp_graph)).candidates[0]

    assert '## Why this might fail' in candidate['body']
    assert 'may be a fee, not an edge' in candidate['body']
    assert '## What past failure this addresses' in candidate['body']
    assert 'PM_fair_value_arb' in candidate['body']
    # The original argument is not lost when the reasoning is appended.
    assert '1c on a 42c contract is 238bps' in candidate['body']

    rendered = forge.render(candidate, [])
    assert 'Why this might fail' in rendered


# ---------------------------------------------------------------------------
# Python still holds the pen
# ---------------------------------------------------------------------------

def test_a_kill_condition_with_no_number_is_refused_by_validate(
        temp_vault, temp_graph, monkeypatch, tmp_path):
    """The model's candidates meet the SAME gate as a hand written one."""
    bad = _good_candidate(
        'pm_vibes',
        kill_condition='retire it if the polymarket_harness says it is bad')
    monkeypatch.setattr(llm_client, 'run_task',
                        _fake_turn(_reply([bad, _good_candidate('pm_ok')])))
    monkeypatch.setattr(forge, 'PROPOSALS_DIR', str(tmp_path))

    outcome = fr.reason(_brief(temp_graph))
    assert len(outcome.candidates) == 2, 'the reasoner does not validate'

    record = forge.generate(outcome.candidates, dict(MINIMAL_GAPS))
    assert record['refused_by_category']['unmeasurable_kill_condition'] == 1
    assert [w['name'] for w in record['written']] == ['pm_ok']
    assert os.path.exists(os.path.join(str(tmp_path), '001-pm-ok.md'))


def test_an_edge_below_the_binary_floor_is_refused(temp_vault, temp_graph,
                                                   monkeypatch, tmp_path):
    """Convention 5, instrument aware: 10bps is under the 20bps binary floor (D-336)."""
    monkeypatch.setattr(llm_client, 'run_task', _fake_turn(
        _reply([_good_candidate('pm_thin', expected_edge_bps=10)])))
    monkeypatch.setattr(forge, 'PROPOSALS_DIR', str(tmp_path))

    outcome = fr.reason(_brief(temp_graph))
    record = forge.generate(outcome.candidates, dict(MINIMAL_GAPS))
    assert record['refused_by_category']['below_min_edge_bps'] == 1
    assert record['written'] == []


# ---------------------------------------------------------------------------
# forge.py wiring: the default path, and the opt in path
# ---------------------------------------------------------------------------

def _explode(*args, **kwargs):
    raise AssertionError('the default path must not spawn a model turn')


def test_the_default_path_is_the_hand_written_list_and_calls_no_model(
        monkeypatch):
    """No flag means EXACTLY what it meant before this module existed."""
    from agents.forge_candidates import CANDIDATES
    monkeypatch.setattr(llm_client, 'run_task', _explode)

    shadow = [{'name': 'shadow_unblock_weather_arb'}]
    candidates, record = forge.collect_candidates(dict(MINIMAL_GAPS), shadow)

    assert record is None, 'no reasoner record when no reasoner was asked for'
    assert candidates == list(CANDIDATES) + shadow


def test_the_reasoner_flag_replaces_the_hand_written_list_only(
        temp_vault, temp_graph, monkeypatch):
    """Shadow repairs are a measurement, so the flag does not touch them."""
    monkeypatch.setattr(llm_client, 'run_task',
                        _fake_turn(_reply([_good_candidate('pm_new')])))
    shadow = [{'name': 'shadow_unblock_weather_arb'}]

    candidates, record = forge.collect_candidates(
        dict(MINIMAL_GAPS), shadow, use_reasoner=True, db_path=temp_graph)

    assert [c['name'] for c in candidates] == ['pm_new',
                                               'shadow_unblock_weather_arb']
    assert record['requested'] is True
    assert record['fell_back_to_deterministic'] is False
    assert record['fallback_reason'] is None
    assert record['status'] == 'ok'


def test_a_dead_turn_falls_back_to_the_deterministic_list_and_says_so(
        temp_vault, temp_graph, monkeypatch):
    """Convention 11 plus convention 20: fall back, and COUNT why."""
    from agents.forge_candidates import CANDIDATES
    monkeypatch.setattr(
        llm_client, 'run_task',
        _fake_turn('', ok=False, error='claude turn exceeded 900s'))

    candidates, record = forge.collect_candidates(
        dict(MINIMAL_GAPS), [], use_reasoner=True, db_path=temp_graph)

    assert candidates == list(CANDIDATES), 'it must not write zero proposals'
    assert record['fell_back_to_deterministic'] is True
    assert record['fallback_reason'] == 'NOT_TESTED'
    assert record['not_tested'] is True
    assert record['dropped_by_category']['llm_turn_could_not_run'] == 1
    assert record['fallback_reason'] in forge.REASONER_FALLBACK_REASONS


def test_an_unusable_reply_falls_back_under_a_different_reason(
        temp_vault, temp_graph, monkeypatch):
    """"Could not run" and "ran and made no sense" are different facts."""
    monkeypatch.setattr(llm_client, 'run_task', _fake_turn('sorry, no.'))
    _candidates, record = forge.collect_candidates(
        dict(MINIMAL_GAPS), [], use_reasoner=True, db_path=temp_graph)
    assert record['fell_back_to_deterministic'] is True
    assert record['fallback_reason'] == 'unusable_reply'
    assert record['not_tested'] is False


# ---------------------------------------------------------------------------
# End to end through main(), including the run log
# ---------------------------------------------------------------------------

@pytest.fixture
def sandboxed_forge(tmp_path, monkeypatch):
    """Point forge's writes and its graveyard reads somewhere disposable."""
    out = tmp_path / 'proposals'
    out.mkdir()
    monkeypatch.setattr(forge, 'PROPOSALS_DIR', str(out))
    monkeypatch.setattr(forge, 'RUN_LOG', str(out / 'forge_runs.jsonl'))
    for name in ('SUMMARY_PATH', 'EVIDENCE_PATH', 'POOLED_PATH'):
        monkeypatch.setattr(forge, name, str(tmp_path / ('no-' + name)))
    return out


def _last_record(out):
    lines = [line for line in
             (out / 'forge_runs.jsonl').read_text().splitlines() if line]
    return json.loads(lines[-1])


def test_the_default_run_log_record_has_no_reasoner_key(
        sandboxed_forge, temp_vault, monkeypatch, capsys):
    """Additive keys are still a change. The default record must not grow one."""
    monkeypatch.setattr(llm_client, 'run_task', _explode)
    assert forge.main([]) == 0
    capsys.readouterr()

    record = _last_record(sandboxed_forge)
    assert 'reasoner' not in record
    assert record['written'], 'the default path still writes proposals'
    # Task 4: the vault is part of the evidence Forge gathers, on every path.
    assert record['gaps_used']['vault']['status'] == 'ok'
    assert 'strike-proxy-floor-direction.md' in json.dumps(
        record['gaps_used']['vault'])


def test_the_reasoner_flag_writes_the_models_candidates_through_validate(
        sandboxed_forge, temp_vault, temp_graph, monkeypatch, capsys):
    monkeypatch.setattr(llm_client, 'run_task', _fake_turn(
        _reply([_good_candidate('pm_reasoned'),
                _good_candidate('pm_unmeasurable',
                                kill_condition='retire it if it feels bad')])))

    assert forge.main(['--reasoner', '--shadow-results', temp_graph]) == 0
    capsys.readouterr()

    record = _last_record(sandboxed_forge)
    assert record['reasoner']['requested'] is True
    assert record['reasoner']['status'] == 'ok'
    assert record['reasoner']['candidates_kept'] == 2
    # Two candidates in, one written: PYTHON refused the other one.
    assert [w['name'] for w in record['written']] == ['pm_reasoned']
    assert record['refused_by_category']['unmeasurable_kill_condition'] == 1
    assert (sandboxed_forge / '001-pm-reasoned.md').exists()
    assert 'Why this might fail' in (
        sandboxed_forge / '001-pm-reasoned.md').read_text()


def test_a_dead_turn_under_the_flag_records_not_tested_in_the_run_log(
        sandboxed_forge, temp_vault, temp_graph, monkeypatch, capsys):
    """It must not crash, and it must not write zero proposals in silence."""
    monkeypatch.setattr(llm_client, 'run_task',
                        _fake_turn('', ok=False, error='no claude on PATH'))

    assert forge.main(['--reasoner', '--shadow-results', temp_graph]) == 0
    err = capsys.readouterr().err
    assert 'NOT_TESTED' in err, 'the fallback has to be loud'

    record = _last_record(sandboxed_forge)
    assert record['reasoner']['fallback_reason'] == 'NOT_TESTED'
    assert record['reasoner']['fell_back_to_deterministic'] is True
    assert record['written'], 'it fell back, so proposals were still written'


def test_the_run_log_stays_json_strict_with_a_reasoner_record(
        sandboxed_forge, temp_vault, temp_graph, monkeypatch, capsys):
    """Convention 19: log_run uses allow_nan=False, so this would raise."""
    monkeypatch.setattr(llm_client, 'run_task',
                        _fake_turn(_reply([_good_candidate('pm_json')])))
    assert forge.main(['--reasoner', '--shadow-results', temp_graph]) == 0
    capsys.readouterr()
    text = (sandboxed_forge / 'forge_runs.jsonl').read_text()
    assert 'NaN' not in text and 'Infinity' not in text
    for line in text.splitlines():
        if line.strip():
            json.loads(line)


def test_a_shadow_db_without_the_tables_is_unreadable_not_empty(
        sandboxed_forge, temp_vault, temp_graph, monkeypatch, capsys):
    """The temp graph DB has no `signals` table. Convention 11 must hold."""
    monkeypatch.setattr(llm_client, 'run_task', _explode)
    assert forge.main(['--shadow-results', temp_graph]) == 0
    err = capsys.readouterr().err
    assert 'unreadable shadow results' in err

    record = _last_record(sandboxed_forge)
    assert record['gaps_used']['shadow'] is None
    assert record['gaps_used']['shadow_error']['error']
    assert record['shadow_candidates_added'] == 0


def test_gaps_only_writes_nothing_and_still_carries_the_vault(
        sandboxed_forge, temp_vault, capsys):
    assert forge.main(['--gaps-only']) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed['vault']['status'] == 'ok'
    assert printed['vault']['total_notes'] == 2
    assert 'spread_eats_edge' in printed['vault']['known_failure_modes']
    assert not os.path.exists(str(sandboxed_forge / 'forge_runs.jsonl'))


def test_a_missing_vault_is_reported_not_silently_empty(monkeypatch, tmp_path):
    """Convention 11 once more: no vault is not the same as no lessons."""
    nowhere = str(tmp_path / 'no-vault')
    monkeypatch.setattr(vault_reader, 'VAULT_ROOT', nowhere)
    monkeypatch.setattr(vault_reader, 'SECTIONS', (
        ('lessons', os.path.join(nowhere, 'Lessons')),
    ))
    summary = forge.load_vault_summary()
    assert summary['status'] == 'ok'
    assert summary['total_notes'] == 0

    rendered = vault_reader.render_context(vault_reader.load_context())
    assert 'directory does not exist yet' in rendered


# ---------------------------------------------------------------------------
# The vault digest (Task 2 of the 2026-08-18 vault-digest work): forge_reasoner
# reads `_DIGEST.md` first instead of the full note tree.
# ---------------------------------------------------------------------------

def _write_digest(root, entries):
    """`entries` is `[(date, filename, verdict, evidence, relevance), ...]`."""
    text = ''.join(vault_digest.format_entry(*e) for e in entries)
    (root / vault_digest.DIGEST_FILENAME).write_text(text)


def test_load_vault_with_a_fresh_digest_reads_the_digest_not_the_full_tree(
        temp_vault):
    _write_digest(temp_vault, [
        ('2026-08-18', 'strike-proxy-floor-direction.md',
         'A bigger floor rejects more windows.', '18,937 evaluations',
         'Do not loosen the floor to buy frequency.'),
    ])
    out = fr.load_vault()
    assert out['status'] == 'ok'
    assert out['digest_status'] == 'ok'
    assert 'A bigger floor rejects more windows.' in out['text']
    assert 'OBSIDIAN VAULT DIGEST' in out['text']
    # The digested note's own RAW BODY (the fixture's LESSON_TEXT, distinct
    # from the digest's own summarised verdict above) must not be re-read in
    # full: that is the entire point of the digest existing. Its filename
    # legitimately appears once, in the digest entry's own heading.
    assert 'A bigger noise floor rejects more windows, never fewer.' \
        not in out['text']


def test_load_vault_surfaces_a_note_the_digest_has_not_absorbed_yet(
        temp_vault):
    # The digest covers only ONE of the two notes the fixture wrote to disk.
    _write_digest(temp_vault, [
        ('2026-08-18', 'strike-proxy-floor-direction.md', 'v', 'e', 'r'),
    ])
    out = fr.load_vault()
    assert out['digest_status'] == 'ok'
    assert 'RECENT NOTES NOT YET IN THE DIGEST' in out['text']
    assert 'fair-value-arb-card.md' in out['text']
    assert out['recent_notes'] == 1


def test_load_vault_with_no_digest_file_falls_back_to_the_full_tree(
        temp_vault):
    """Task 2.4: an absent digest must never fail a Forge cycle."""
    out = fr.load_vault()
    assert out['status'] == 'ok'
    assert out['digest_status'] == 'missing'
    assert 'DIGEST MISSING' in out['text']
    # The pre-digest behaviour: everything is still there.
    assert 'strike-proxy-floor-direction.md' in out['text']
    assert 'A bigger noise floor rejects more windows' in out['text']


def test_load_vault_with_a_stale_digest_falls_back_to_the_full_tree(
        temp_vault):
    _write_digest(temp_vault, [
        ('2020-01-01', 'strike-proxy-floor-direction.md', 'v', 'e', 'r'),
    ])
    out = fr.load_vault()
    assert out['digest_status'] == 'stale'
    assert 'DIGEST STALE' in out['text']
    assert 'fair-value-arb-card.md' in out['text']


def test_load_vault_with_a_recent_digest_is_not_stale(temp_vault):
    _write_digest(temp_vault, [
        (vault_digest.today(), 'strike-proxy-floor-direction.md', 'v', 'e', 'r'),
    ])
    out = fr.load_vault()
    assert out['digest_status'] == 'ok'


def test_vault_ctx_line_reports_digest_size_and_graph_count(temp_vault,
                                                             temp_graph):
    _write_digest(temp_vault, [
        ('2026-08-18', 'strike-proxy-floor-direction.md', 'v', 'e', 'r'),
    ])
    brief = _brief(temp_graph)
    assert brief['vault_ctx_line'].startswith('vault_ctx: digest=')
    assert 'recent=' in brief['vault_ctx_line']
    assert 'graph=%d' % brief['hypothesis_graph']['n_failed_total'] in \
        brief['vault_ctx_line']


def test_vault_ctx_line_names_the_fallback_when_the_digest_is_missing(
        temp_vault, temp_graph):
    brief = _brief(temp_graph)
    assert 'vault_ctx: digest=MISSING' in brief['vault_ctx_line']


def test_render_brief_carries_the_vault_ctx_line_as_visible_proof(temp_vault,
                                                                   temp_graph):
    """Task 2.5: the line has to be IN the artifact the model reads, not just
    logged on the side, or it proves nothing about what THIS cycle read."""
    _write_digest(temp_vault, [
        ('2026-08-18', 'strike-proxy-floor-direction.md', 'v', 'e', 'r'),
    ])
    brief = _brief(temp_graph)
    rendered = fr.render_brief(brief)
    assert rendered.startswith('vault_ctx: digest=')


def test_sqlite_row_reading_is_read_only(temp_graph):
    """The reasoner must not hold a write handle on a live tape."""
    conn = hg.connect(temp_graph, read_only=True)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute(
                "INSERT INTO hypothesis_graph "
                "(ts, strategy_name, hypothesis, status) "
                "VALUES (1, 'x', 'y', 'UNTESTED')")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Proposal numbering
# ---------------------------------------------------------------------------
#
# Added after the first real reasoner run produced a second 001 through 007
# beside the existing ones. Nothing was overwritten (the slug is part of the
# filename) but "proposal 005" stopped identifying a document, and
# corridor_pair_live.py cites proposal 005 by number.

def test_next_free_index_continues_past_what_is_on_disk(tmp_path):
    for name in ('001-a.md', '016-b.md', '007-c.md'):
        (tmp_path / name).write_text('x')
    assert forge.next_free_index(str(tmp_path)) == 17


def test_next_free_index_ignores_non_proposals(tmp_path):
    (tmp_path / 'README.md').write_text('x')
    (tmp_path / 'forge_runs.jsonl').write_text('x')
    (tmp_path / '003-real.md').write_text('x')
    assert forge.next_free_index(str(tmp_path)) == 4


def test_next_free_index_on_an_empty_dir_is_one(tmp_path):
    assert forge.next_free_index(str(tmp_path)) == 1


def test_next_free_index_on_a_missing_dir_is_one(tmp_path):
    assert forge.next_free_index(str(tmp_path / 'nope')) == 1


def test_the_live_proposals_dir_has_no_duplicate_numbers():
    """The regression itself, pinned against the real directory."""
    import collections as _collections
    numbers = _collections.Counter()
    for name in os.listdir(forge.PROPOSALS_DIR):
        if not name.endswith('.md'):
            continue
        head = name.split('-', 1)[0]
        if len(head) == 3 and head.isdigit():
            numbers[head] += 1
    duplicates = {k: v for k, v in numbers.items() if v > 1}
    assert not duplicates, 'duplicate proposal numbers on disk: %r' % duplicates


def test_next_free_index_honours_a_monkeypatched_proposals_dir(tmp_path,
                                                               monkeypatch):
    """The late-binding bug, pinned.

    A `proposals_dir=PROPOSALS_DIR` default argument binds the module global
    once at import, so a test that redirects the directory would silently get
    the real repo's numbering. That is worse than a wrong number: it is a test
    that passes while reading production state.
    """
    monkeypatch.setattr(forge, 'PROPOSALS_DIR', str(tmp_path))
    (tmp_path / '004-x.md').write_text('x')
    assert forge.next_free_index() == 5


def test_a_rerun_overwrites_in_place_instead_of_appending(tmp_path,
                                                          monkeypatch):
    """The bug that appeared in BOTH directions in one day.

    `start_index=1` made the reasoner produce a second 001-007. Changing the
    default to next-free made the DETERMINISTIC path produce 024-028 carrying
    the same five slugs as 001-005 three minutes later. Numbering by slug
    identity is the fix for both, and this is the property that proves it:
    running the same candidates twice leaves the same files.
    """
    monkeypatch.setattr(forge, 'PROPOSALS_DIR', str(tmp_path))
    candidate = _good_candidate()
    gaps = {'known_strategies': []}

    first = forge.generate([candidate], gaps)
    after_first = sorted(p for p in os.listdir(str(tmp_path))
                         if p.endswith('.md'))
    second = forge.generate([candidate], gaps)
    after_second = sorted(p for p in os.listdir(str(tmp_path))
                          if p.endswith('.md'))

    assert after_first == after_second, (
        'a re-run duplicated proposals: %r -> %r' % (after_first, after_second))
    assert len(after_second) == 1
    assert first['written'][0]['path'] == second['written'][0]['path']


def test_a_genuinely_new_proposal_still_takes_the_next_free_number(
        tmp_path, monkeypatch):
    monkeypatch.setattr(forge, 'PROPOSALS_DIR', str(tmp_path))
    (tmp_path / '007-something-else.md').write_text('x')
    forge.generate([_good_candidate()], {'known_strategies': []})
    names = sorted(p for p in os.listdir(str(tmp_path)) if p.endswith('.md'))
    assert names[0] == '007-something-else.md'
    assert names[1].startswith('008-')


def test_existing_numbers_by_slug_resolves_a_duplicate_to_the_lowest(tmp_path):
    """So a repair collapses onto the original, not onto the accident."""
    (tmp_path / '005-foo.md').write_text('x')
    (tmp_path / '028-foo.md').write_text('x')
    assert forge.existing_numbers_by_slug(str(tmp_path))['foo'] == 5


def test_the_live_proposals_dir_has_no_duplicate_slugs():
    """The 024-028 regression, pinned against the real directory."""
    import collections as _collections
    slugs = _collections.Counter()
    for name in os.listdir(forge.PROPOSALS_DIR):
        if not name.endswith('.md'):
            continue
        head, _, tail = name[:-3].partition('-')
        if len(head) == 3 and head.isdigit() and tail:
            slugs[tail] += 1
    duplicates = {k: v for k, v in slugs.items() if v > 1}
    assert not duplicates, 'duplicate proposal slugs on disk: %r' % duplicates
