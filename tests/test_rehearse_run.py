"""Tests for the rehearsal script.

Two things matter about it and nothing else does: it must never send email,
and it must never write state. It reads the real config on purpose, so a
rehearsal that could touch either would be a rehearsal that could damage
production data on a machine where the real config is the real config.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts import rehearse_run


class TestReadTitles:
    def test_a_title_and_a_price_are_enough(self, tmp_path):
        path = tmp_path / "t.txt"
        path.write_text("2024 Panini Prizm Caleb Williams #301 | 8.99\n")
        assert rehearse_run.read_titles(path) == [
            ("2024 Panini Prizm Caleb Williams #301", 8.99, None)
        ]

    def test_blank_shipping_means_unknown_not_free(self):
        # The distinction the whole pipeline is careful about; a rehearsal
        # that flattened it would rehearse the wrong thing.
        path = Path(__file__).parent / "_tmp_titles.txt"
        path.write_text("Card A | 5.00 |\nCard B | 5.00 | 0\n")
        try:
            rows = rehearse_run.read_titles(path)
        finally:
            path.unlink()
        assert rows[0][2] is None
        assert rows[1][2] == 0.0

    def test_comments_and_blank_lines_are_skipped(self, tmp_path):
        path = tmp_path / "t.txt"
        path.write_text("# a note\n\nCard A | 5.00\n")
        assert len(rehearse_run.read_titles(path)) == 1

    def test_a_malformed_line_says_which_line(self, tmp_path):
        path = tmp_path / "t.txt"
        path.write_text("Card A | 5.00\njust a title\n")
        with pytest.raises(SystemExit) as caught:
            rehearse_run.read_titles(path)
        assert ":2:" in str(caught.value)


class TestItCannotSend:
    def test_the_send_hook_raises_rather_than_silently_doing_nothing(self):
        # A no-op stub would let a future edit that dropped the dry-run flag
        # go unnoticed. This fails loudly instead.
        with pytest.raises(AssertionError):
            rehearse_run._refuse_to_send("subject", "body", "a", "b", "c")


class TestSampleTitles:
    def test_the_sample_exercises_the_paths_worth_rehearsing(self):
        titles = " ".join(title for title, _, _ in rehearse_run.SAMPLE_TITLES).lower()
        for marker in ("auto", "patch", "reprint", "lot of"):
            assert marker in titles, marker

    def test_some_shipping_is_unknown(self):
        assert any(shipping is None for _, _, shipping in rehearse_run.SAMPLE_TITLES)

    def test_prices_span_the_ceilings(self):
        prices = [price for _, price, _ in rehearse_run.SAMPLE_TITLES]
        assert min(prices) < 5.0
        assert max(prices) > 100.0
