import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from unittest import mock

import main


def problems(prefix, count):
    return [{'title': f'{prefix} {i}', 'slug': f'{prefix.lower()}-{i}'} for i in range(count)]


class PickerTests(unittest.TestCase):
    def setUp(self):
        self.bank = {
            'Complete': {
                'Easy': problems('EasyA', 5),
                'Medium': problems('MediumA', 5),
                'Hard': problems('HardA', 5),
            },
            'Partial': {
                'Easy': problems('EasyB', 2),
                'Medium': problems('MediumB', 5),
                'Hard': problems('HardB', 5),
            },
            'No Easy': {
                'Easy': [],
                'Medium': problems('MediumC', 5),
                'Hard': problems('HardC', 5),
            },
        }
        self.state = {'v': 1, 'posts': 0, 'progress': {}}

    def advance(self, batch):
        self.state['progress'][batch['key']] = batch['next_cursor']

    def test_difficulty_parser_accepts_one_or_two_and_canonicalizes_order(self):
        self.assertEqual(main.parse_difficulties('medium'), ('Medium',))
        self.assertEqual(main.parse_difficulties('medium + easy'), ('Easy', 'Medium'))
        with self.assertRaises(ValueError):
            main.parse_difficulties('Easy,Easy')
        with self.assertRaises(ValueError):
            main.parse_difficulties('Easy,Medium,Hard')

    def test_scheduled_run_respects_configured_weekday_and_timezone(self):
        monday_evening_utc = datetime(2026, 11, 16, 18, 30, tzinfo=timezone.utc)
        self.assertTrue(
            main.is_post_day('schedule', 'Monday', 'Europe/London', monday_evening_utc)
        )
        self.assertFalse(
            main.is_post_day('schedule', 'Tuesday', 'Europe/London', monday_evening_utc)
        )
        self.assertTrue(
            main.is_post_day('workflow_dispatch', 'Tuesday', 'Europe/London', monday_evening_utc)
        )
        with self.assertRaises(ValueError):
            main.is_post_day('schedule', 'Funday', 'Europe/London', monday_evening_utc)

    def test_single_mode_includes_partial_topics_and_skips_empty_ones(self):
        self.assertEqual(main.eligible_topics(self.bank, ('Easy',)), ['Complete', 'Partial'])

    def test_dual_mode_requires_exactly_five_of_both_difficulties(self):
        self.assertEqual(main.eligible_topics(self.bank, ('Easy', 'Medium')), ['Complete'])
        self.assertEqual(
            main.eligible_topics(self.bank, ('Medium', 'Hard')),
            ['Complete', 'Partial', 'No Easy'],
        )

    def test_changing_count_resumes_at_next_problem_without_repeating(self):
        first = main.select_batch(self.bank, self.state, ('Easy',), 2)
        self.assertEqual([p['slug'] for p in first['problems']['Easy']], ['easya-0', 'easya-1'])
        self.advance(first)

        second = main.select_batch(self.bank, self.state, ('Easy',), 3)
        self.assertEqual(
            [p['slug'] for p in second['problems']['Easy']],
            ['easya-2', 'easya-3', 'easya-4'],
        )
        self.assertEqual(second['next_cursor'], ['Partial', 0, 1])

    def test_dual_mode_selects_equal_counts_and_wraps_as_one_rotation(self):
        first = main.select_batch(self.bank, self.state, ('Easy', 'Medium'), 2)
        self.assertEqual(len(first['problems']['Easy']), 2)
        self.assertEqual(len(first['problems']['Medium']), 2)
        self.advance(first)

        reordered = main.parse_difficulties('Medium,Easy')
        second = main.select_batch(self.bank, self.state, reordered, 3)
        self.assertEqual(len(second['problems']['Easy']), 3)
        self.assertEqual(len(second['problems']['Medium']), 3)
        self.assertEqual(second['next_cursor'], ['Complete', 0, 2])

    def test_each_difficulty_combination_has_an_independent_cursor(self):
        medium = main.select_batch(self.bank, self.state, ('Medium',), 5)
        self.advance(medium)
        self.assertEqual(medium['next_cursor'], ['Partial', 0, 1])

        dual = main.select_batch(self.bank, self.state, ('Easy', 'Medium'), 1)
        resumed_medium = main.select_batch(self.bank, self.state, ('Medium',), 1)
        self.assertEqual(dual['topic'], 'Complete')
        self.assertEqual(resumed_medium['topic'], 'Partial')

    def test_state_is_compact_single_line_json(self):
        self.state['progress']['Easy'] = ['Complete', 2, 1]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            main.write_state(self.state, path)
            raw = path.read_text(encoding='utf-8')
            self.assertEqual(raw.count('\n'), 1)
            self.assertNotIn(' ', raw)
            self.assertEqual(json.loads(raw), self.state)
            self.assertEqual(main.read_state(path), self.state)

    def test_real_dual_rotation_uses_every_eligible_problem_once(self):
        bank = json.loads(main.PROBLEMS_FILE.read_text(encoding='utf-8'))
        difficulties = ('Easy', 'Medium')
        topics = main.eligible_topics(bank, difficulties)
        expected = {
            difficulty: {
                problem['slug']
                for topic in topics
                for problem in bank[topic][difficulty]
            }
            for difficulty in difficulties
        }
        seen = {difficulty: [] for difficulty in difficulties}
        counts = (2, 1, 3)

        for run in range(100):
            batch = main.select_batch(bank, self.state, difficulties, counts[run % len(counts)])
            for difficulty in difficulties:
                seen[difficulty].extend(p['slug'] for p in batch['problems'][difficulty])
            self.advance(batch)
            if batch['next_cursor'][2] == 2:
                break
        else:
            self.fail('The first dual-difficulty rotation did not finish')

        for difficulty in difficulties:
            self.assertEqual(len(seen[difficulty]), 70)
            self.assertEqual(len(seen[difficulty]), len(set(seen[difficulty])))
            self.assertEqual(set(seen[difficulty]), expected[difficulty])

    def test_successful_post_writes_state_and_later_push_does_not_repost(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / 'state.json'
            environment = {
                'DIFFICULTIES': 'Easy,Medium',
                'QUESTIONS_PER_DIFFICULTY': '2',
                'GITHUB_EVENT_NAME': 'workflow_dispatch',
                'DISCORD_WEBHOOK_URL': 'https://example.invalid/webhook',
                'STATE_FILE': str(state_path),
            }
            with mock.patch.dict(os.environ, environment, clear=True), \
                    mock.patch.object(main, 'post_to_discord') as post, \
                    redirect_stdout(StringIO()):
                main.main()
                os.environ['GITHUB_EVENT_NAME'] = 'push'
                main.main()

            saved = json.loads(state_path.read_text(encoding='utf-8'))
            self.assertEqual(saved['posts'], 1)
            self.assertEqual(saved['progress']['Easy+Medium'], ['Arrays & Hashing', 2, 1])
            post.assert_called_once()

    def test_dry_run_never_creates_state(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / 'state.json'
            environment = {
                'DIFFICULTIES': 'Easy',
                'QUESTIONS_PER_DIFFICULTY': '1',
                'GITHUB_EVENT_NAME': 'workflow_dispatch',
                'DRY_RUN': 'true',
                'STATE_FILE': str(state_path),
            }
            with mock.patch.dict(os.environ, environment, clear=True), redirect_stdout(StringIO()):
                main.main()
            self.assertFalse(state_path.exists())


if __name__ == '__main__':
    unittest.main()
