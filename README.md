# Weekly LeetCode Challenge (Discord + GitHub Actions)

A dependency-free Python picker that posts a weekly LeetCode batch to Discord.
It remembers the exact topic and problem offset in a compact `state.json`, so
changing the schedule or batch size does not rewind the current rotation.

## Setup

1. Create a private GitHub repository and add the `DISCORD_WEBHOOK_URL` secret
   under **Settings > Secrets and variables > Actions**. Do this before adding
   the workflow if you want its initial run to post successfully.
2. Upload this repository, including `.github/workflows/weekly.yml`.
3. The push that first adds the workflow posts the first batch immediately and
   commits the generated `state.json`. Later pushes of the workflow file see
   that state and do not create an extra post.
4. Future posts use `POST_DAY`. The workflow checks daily at 18:30 in
   `Europe/London`, follows daylight-saving changes, and exits without posting
   or changing state on the other six days.

The workflow needs `contents: write` so it can commit `state.json`. If the
default branch is protected against direct pushes, allow the GitHub Actions bot
to push this state commit or use a branch-protection exception for it.

## Configuration

Edit `.github/workflows/weekly.yml`:

```yaml
schedule:
  - cron: '30 18 * * *'      # check daily at 18:30
    timezone: 'Europe/London'

env:
  POST_DAY: 'Monday'
  TIMEZONE: 'Europe/London'
  DIFFICULTIES: 'Easy,Medium'
  QUESTIONS_PER_DIFFICULTY: '2'
```

- `DIFFICULTIES` accepts one or two unique values from `Easy`, `Medium`, and
  `Hard`. Their order does not create a new rotation.
- `QUESTIONS_PER_DIFFICULTY` is 1–5 and applies equally to every selected
  difficulty. With `Easy,Medium` and `2`, a normal post contains two Easy and
  two Medium questions. A topic's final post can be smaller (for example, one
  Easy and one Medium) so the next topic starts cleanly on the following run.
- `POST_DAY` accepts `Monday` through `Sunday`. Change this value to move the
  weekly post without altering saved progress.
- The cron time is 18:30. Change its minute/hour fields to choose another time.
  Keep the workflow `timezone` and the `TIMEZONE` environment value identical.
  GitHub supports a timezone on scheduled workflows, so the local time remains
  stable across daylight-saving changes. See the [GitHub schedule syntax documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#onschedule).

Changing any of these settings leaves existing progress intact. Each difficulty
combination has its own saved cursor: switching from `Medium` to `Easy,Medium`
starts that combination's first rotation, while switching back to `Medium`
resumes its earlier cursor.

## Topic eligibility

Single-difficulty mode uses every topic that has at least one question at that
difficulty. This includes partial banks such as the two Easy Backtracking
questions, while empty banks are skipped.

Dual-difficulty mode is stricter: a topic is used only when **both selected
difficulties contain exactly five questions**. Thus `Easy,Medium` excludes
Tries, Backtracking, Advanced Graphs, and 2-D Dynamic Programming from the
current bank, but those topics remain available in eligible single-difficulty
rotations. `Medium,Hard` currently uses all 18 topics.

## State and progression

After Discord accepts a post, the script updates a single-line JSON file similar
to this:

```json
{"posts":3,"progress":{"Easy+Medium":["Two Pointers",2,1]},"v":1}
```

For each setup, the array stores the next topic, the next zero-based question
offset, and the cycle number. Because progression is stored per problem rather
than calculated from calendar weeks, changing from two questions to one or
three continues at the next unseen question. After the final eligible topic is
finished, its cursor wraps to cycle 2.

State advances only after a successful Discord response. Preview runs never
write it. The workflow serializes runs and checks out the latest branch state
before selecting, which prevents ordinary overlapping runs from choosing the
same batch.

## Manual preview or post

Open **Actions > Weekly LeetCode Challenge > Run workflow**. Leave `dry_run`
checked to preview without posting or changing state. Uncheck it to post the
previewed batch and advance its cursor.

For a local preview:

```bash
DRY_RUN=true DIFFICULTIES='Easy,Medium' QUESTIONS_PER_DIFFICULTY=2 python main.py
```

Legacy `DIFFICULTY` and `QUESTIONS_PER_WEEK` environment variables are accepted
as fallbacks, but the new names are preferred.

## Question format

Each entry in `problems.json` has a title and a LeetCode URL slug:

```json
{"title":"Two Sum","slug":"two-sum"}
```

Links are constructed as `https://leetcode.com/problems/<slug>/`.

## Operational notes

- GitHub scheduled workflows run from the default branch and can occasionally
  be delayed. Public repositories can have schedules disabled after 60 days of
  inactivity; a private repository avoids that inactivity rule.
- There is an unavoidable small failure window between Discord accepting a post
  and GitHub accepting the state commit. If that commit is rejected, inspect
  the Discord channel before rerunning to avoid a duplicate.
- The dataset is a curated snapshot of free problems. Availability and assigned
  difficulty can change on LeetCode.

Question topics and many original candidates were cross-checked against the
community-maintained [NeetCode 250 list](https://github.com/shubhamx939/LearnDSA/blob/main/NeetCode%20250%20Questions.markdown)
and [NeetCode practice list](https://neetcode.io/practice).
