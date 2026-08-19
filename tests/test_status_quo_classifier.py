"""Tests for `strategies.polymarket.status_quo_classifier`.

The minimum test list is Task 1 of
`docs/handoffs/from-raven/2026-08-18-proposal-028.md`, reproduced exactly
below, plus the adversarial "no false STATUS_QUO on a regime-change-shaped
question" case the handoff calls out by name.
"""
from strategies.polymarket.status_quo_classifier import (
    CHANGE_EVENT, STATUS_QUO, UNKNOWN, Classification, classify)


class TestStatusQuoExamples:

    def test_putin_remain_president_until_2027(self):
        c = classify('Will Vladimir Putin remain president of Russia '
                     'until 2027?')
        assert c.label == STATUS_QUO
        assert c.rule

    def test_iranian_government_remain_intact(self):
        c = classify('Will the Iranian government remain intact through '
                     'December 2026?')
        assert c.label == STATUS_QUO

    def test_biden_remain_president_until_january_2027(self):
        c = classify('Will Joe Biden remain president until January 2027?')
        assert c.label == STATUS_QUO


class TestChangeEventExamples:

    def test_trump_win_2028_election(self):
        c = classify('Will Donald Trump win the 2028 presidential election?')
        assert c.label == CHANGE_EVENT
        assert c.rule

    def test_xi_resign_by_2027(self):
        c = classify('Will Xi Jinping resign by 2027?')
        assert c.label == CHANGE_EVENT

    def test_uk_hold_general_election(self):
        c = classify('Will the UK hold a general election before December?')
        assert c.label == CHANGE_EVENT


class TestUnknownExamples:

    def test_earthquake_no_continuity_shape(self):
        c = classify('Will there be a major earthquake this year?')
        assert c.label == UNKNOWN
        assert c.rule

    def test_bitcoin_price_numeric_not_binary_continuity(self):
        c = classify('What will be the price of Bitcoin on December 31?')
        assert c.label == UNKNOWN
        assert c.rule == 'numeric_shape'


class TestNoFalseStatusQuoOnRegimeChangeShapedQuestions:
    """The handoff names this failure mode explicitly: a question that
    contains BOTH a continuity word and a change trigger must classify as
    CHANGE_EVENT, never STATUS_QUO. This is the exact shape of the reference
    wallet's one catastrophic loss (see the module docstring)."""

    def test_remain_in_power_after_a_coup_is_change_event(self):
        c = classify('Will Assad remain in power after being overthrown in '
                     'a coup by 2027?')
        assert c.label == CHANGE_EVENT
        assert c.rule == 'coup'

    def test_remain_in_power_pending_impeachment_is_change_event(self):
        c = classify('Will the President remain in power despite an '
                     'impeachment vote in 2027?')
        assert c.label == CHANGE_EVENT
        assert c.rule == 'impeach'

    def test_still_in_office_after_removal_attempt_is_change_event(self):
        c = classify('Will the Prime Minister still be in office after '
                     'being removed by Parliament in 2027?')
        assert c.label == CHANGE_EVENT
        assert c.rule == 'removed'


class TestNegatedRemovalPhrase:

    def test_not_be_removed_with_date_is_status_quo(self):
        c = classify('Will the Prime Minister not be removed from office '
                     'by 2027?')
        assert c.label == STATUS_QUO
        assert c.rule == 'negated_change_phrase'

    def test_not_be_removed_without_date_is_unknown(self):
        c = classify('Will the Prime Minister not be removed from office?')
        assert c.label == UNKNOWN
        assert c.rule == 'negated_change_phrase_no_date'


class TestDateRequirement:
    """Task 1 rule 1: STATUS_QUO is a DATED statement. A continuity keyword
    with no date signal anywhere degrades to UNKNOWN rather than being forced
    into STATUS_QUO (rule 3: honest default)."""

    def test_remain_in_power_no_date_is_unknown(self):
        # 'remain in power' hits the multi-word phrase table before the
        # single-word keyword list ever runs - see the module docstring.
        c = classify('Will the government remain in power?')
        assert c.label == UNKNOWN
        assert c.rule == 'phrase_no_date:remain_in_power'

    def test_resolution_date_argument_is_authoritative(self):
        # No year or month anywhere in the question text; the caller-supplied
        # resolution_date is what makes this STATUS_QUO instead of UNKNOWN.
        c = classify('Will the government remain in power?',
                     resolution_date='2027-03-31')
        assert c.label == STATUS_QUO
        assert c.rule == 'phrase:remain_in_power'


class TestHonestDefault:

    def test_empty_question_is_unknown(self):
        c = classify('')
        assert c.label == UNKNOWN

    def test_ambiguous_question_with_no_markers_is_unknown(self):
        c = classify('Will the weather be nice on Tuesday?')
        assert c.label == UNKNOWN
        assert c.rule == 'no_clear_shape'


class TestClassificationShape:

    def test_returns_classification_with_original_question(self):
        c = classify('Will Xi Jinping resign by 2027?')
        assert isinstance(c, Classification)
        assert c.question == 'Will Xi Jinping resign by 2027?'
        assert c.is_status_quo is False

    def test_status_quo_result_reports_is_status_quo(self):
        c = classify('Will Putin remain president until 2027?')
        assert c.is_status_quo is True

    def test_never_raises_on_none_question(self):
        c = classify(None)
        assert c.label == UNKNOWN
