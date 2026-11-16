"""Post the next stateful LeetCode batch to a Discord webhook (stdlib only)."""

import json
import os
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DIFFICULTIES = ('Easy', 'Medium', 'Hard')
DAYS = {day.lower(): index for index, day in enumerate(
    ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')
)}
ROOT = Path(__file__).resolve().parent
PROBLEMS_FILE = ROOT / 'problems.json'
STATE_FILE = ROOT / 'state.json'


def parse_difficulties(value):
    """Return one or two unique difficulties in a stable order."""
    requested = [part.strip().capitalize() for part in value.replace('+', ',').split(',') if part.strip()]
    if not 1 <= len(requested) <= 2:
        raise ValueError('DIFFICULTIES must contain one or two comma-separated values')
    if len(set(requested)) != len(requested):
        raise ValueError('DIFFICULTIES cannot contain duplicates')
    invalid = [difficulty for difficulty in requested if difficulty not in DIFFICULTIES]
    if invalid:
        raise ValueError('DIFFICULTIES may only contain Easy, Medium, and Hard')
    return tuple(difficulty for difficulty in DIFFICULTIES if difficulty in requested)


def read_state(path=STATE_FILE):
    if not path.exists():
        return {'v': 1, 'posts': 0, 'progress': {}}
    try:
        state = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f'Cannot read valid JSON state from {path.name}') from exc
    if not isinstance(state, dict) or state.get('v') != 1:
        raise ValueError(f'{path.name} has an unsupported state format')
    if not isinstance(state.get('posts'), int) or state['posts'] < 0:
        raise ValueError(f'{path.name} has an invalid posts count')
    if not isinstance(state.get('progress'), dict):
        raise ValueError(f'{path.name} has invalid progress data')
    return state


def write_state(state, path=STATE_FILE):
    """Write a small, deterministic, single-line state file."""
    path.write_text(json.dumps(state, separators=(',', ':'), sort_keys=True) + '\n', encoding='utf-8')


def eligible_topics(bank, difficulties):
    """Filter topics according to the single/dual-difficulty rules."""
    topics = []
    for topic, levels in bank.items():
        try:
            sizes = [len(levels[difficulty]) for difficulty in difficulties]
        except (KeyError, TypeError) as exc:
            raise ValueError(f'Invalid problem bank entry for {topic}') from exc
        if len(difficulties) == 1:
            if sizes[0] > 0:
                topics.append(topic)
        elif all(size == 5 for size in sizes):
            topics.append(topic)
    return topics


def rotation_key(difficulties):
    return '+'.join(difficulties)


def is_post_day(event, post_day, timezone, now=None):
    """Return whether this run is eligible to post under the weekday setting."""
    day = post_day.strip().lower()
    if day not in DAYS:
        raise ValueError('POST_DAY must be a weekday name, e.g. Monday')
    try:
        zone = ZoneInfo(timezone)
    except ZoneInfoNotFoundError:
        raise ValueError(f'Unknown TIMEZONE: {timezone}') from None
    if event != 'schedule':
        return True
    current = now.astimezone(zone) if now is not None else datetime.now(zone)
    return current.weekday() == DAYS[day]


def current_position(state, key, topics):
    """Load a cursor, falling forward safely if its topic is no longer eligible."""
    saved = state['progress'].get(key)
    if saved is None:
        return 0, 0, 1
    if not (isinstance(saved, list) and len(saved) == 3):
        raise ValueError(f'Invalid saved progress for {key}')
    topic, offset, cycle = saved
    if not isinstance(offset, int) or offset < 0 or not isinstance(cycle, int) or cycle < 1:
        raise ValueError(f'Invalid saved progress for {key}')
    if topic in topics:
        return topics.index(topic), offset, cycle
    # A bank edit can make a saved dual-difficulty topic ineligible. Start at
    # the first currently eligible topic without discarding the other cursors.
    return 0, 0, cycle


def select_batch(bank, state, difficulties, count):
    """Select a batch and return it with the cursor to save after posting."""
    topics = eligible_topics(bank, difficulties)
    if not topics:
        if len(difficulties) == 2:
            joined = ' + '.join(difficulties)
            raise ValueError(f'No topics contain exactly 5 {joined} questions')
        raise ValueError(f'No {difficulties[0]} problems in problems.json')

    key = rotation_key(difficulties)
    topic_index, offset, cycle = current_position(state, key, topics)
    topic = topics[topic_index]
    levels = bank[topic]
    topic_size = len(levels[difficulties[0]])

    # Normalize state left at a topic boundary by an interrupted/older run.
    if offset >= topic_size:
        topic_index = (topic_index + 1) % len(topics)
        if topic_index == 0:
            cycle += 1
        topic = topics[topic_index]
        levels = bank[topic]
        topic_size = len(levels[difficulties[0]])
        offset = 0

    amount = min(count, topic_size - offset)
    selected = {difficulty: levels[difficulty][offset:offset + amount]
                for difficulty in difficulties}
    if any(len(problems) != amount for problems in selected.values()):
        raise ValueError(f'{topic} does not have aligned difficulty banks')

    next_offset = offset + amount
    next_topic_index = topic_index
    next_cycle = cycle
    if next_offset == topic_size:
        next_topic_index = (topic_index + 1) % len(topics)
        next_offset = 0
        if next_topic_index == 0:
            next_cycle += 1

    next_cursor = [topics[next_topic_index], next_offset, next_cycle]
    return {
        'key': key,
        'topic': topic,
        'topic_number': topic_index + 1,
        'topic_count': len(topics),
        'cycle': cycle,
        'problems': selected,
        'next_cursor': next_cursor,
    }


def make_payload(batch, post_number):
    difficulties = tuple(batch['problems'])
    lines = []
    for difficulty in difficulties:
        if len(difficulties) == 2:
            lines.append(f'**{difficulty}**')
        lines.extend(
            f'{i}. [{problem["title"]}](https://leetcode.com/problems/{problem["slug"]}/)'
            for i, problem in enumerate(batch['problems'][difficulty], 1)
        )
        if len(difficulties) == 2 and difficulty != difficulties[-1]:
            lines.append('')

    description = '\n'.join(lines)
    if len(description) > 4096:
        raise ValueError('Discord embed too long; lower QUESTIONS_PER_DIFFICULTY')

    difficulty_label = ' + '.join(difficulties)
    title = f'LeetCode Challenge #{post_number} | {batch["topic"]} | {difficulty_label}'
    per_difficulty = len(next(iter(batch['problems'].values())))
    total = per_difficulty * len(difficulties)
    footer = (f'{total} question(s) | Cycle {batch["cycle"]} | '
              f'Topic {batch["topic_number"]}/{batch["topic_count"]}')
    payload = {
        'allowed_mentions': {'parse': []},
        'embeds': [{'title': title, 'description': description, 'footer': {'text': footer}}],
    }
    return title, payload


def print_batch(title, batch):
    print(title)
    for difficulty, problems in batch['problems'].items():
        if len(batch['problems']) == 2:
            print(f'{difficulty}:')
        for problem in problems:
            print(f'- {problem["title"]}: https://leetcode.com/problems/{problem["slug"]}/')


def post_to_discord(payload, webhook):
    request = Request(
        webhook,
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json', 'User-Agent': 'leetcode-weekly/2.0'},
        method='POST',
    )
    try:
        with urlopen(request, timeout=20) as response:
            if response.status not in (200, 204):
                raise RuntimeError(f'Webhook returned HTTP {response.status}')
    except HTTPError as exc:
        raise RuntimeError(f'Discord webhook returned HTTP {exc.code}') from None
    except URLError:
        raise RuntimeError('Unable to reach Discord webhook') from None


def main():
    difficulties = parse_difficulties(
        os.getenv('DIFFICULTIES', os.getenv('DIFFICULTY', 'Medium'))
    )
    raw_count = os.getenv('QUESTIONS_PER_DIFFICULTY', os.getenv('QUESTIONS_PER_WEEK', '2'))
    try:
        count = int(raw_count)
    except ValueError:
        raise ValueError('QUESTIONS_PER_DIFFICULTY must be a whole number') from None
    if not 1 <= count <= 5:
        raise ValueError('QUESTIONS_PER_DIFFICULTY must be between 1 and 5')

    event = os.getenv('GITHUB_EVENT_NAME', 'workflow_dispatch')
    state_path = Path(os.getenv('STATE_FILE', STATE_FILE))
    if event == 'push' and state_path.exists():
        print('State already exists; initialization post skipped.')
        return
    post_day = os.getenv('POST_DAY', 'Monday')
    timezone = os.getenv('TIMEZONE', 'Europe/London')
    if not is_post_day(event, post_day, timezone):
        print(f'Not {post_day.strip().title()}; no message sent and state was not changed.')
        return

    bank = json.loads(PROBLEMS_FILE.read_text(encoding='utf-8'))
    state = read_state(state_path)
    batch = select_batch(bank, state, difficulties, count)
    title, payload = make_payload(batch, state['posts'] + 1)
    print_batch(title, batch)

    if os.getenv('DRY_RUN', 'false').lower() == 'true':
        print('DRY_RUN: nothing posted and state was not changed.')
        return

    webhook = os.environ.get('DISCORD_WEBHOOK_URL')
    if not webhook:
        raise ValueError('Set the DISCORD_WEBHOOK_URL GitHub Actions secret')
    post_to_discord(payload, webhook)

    state['posts'] += 1
    state['progress'][batch['key']] = batch['next_cursor']
    write_state(state, state_path)
    print(f'Posted successfully; progress saved to {state_path.name}.')


if __name__ == '__main__':
    main()
