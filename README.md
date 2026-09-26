# gitstat

Developer contribution analytics for a GitHub repository, built on `git` and `gh`.

It shows how each person contributes through pull requests, reviews, comments and commits, how that changes over time, and how their numbers compare with the team. Managers can use it for a team-wide view. Engineers can use it to check their own habits.

## Requirements

- Python 3.9+ (standard library only)
- [`gh`](https://cli.github.com/), authenticated with `gh auth login`
- `git`, for commit stats from a local clone (without a clone, commits come from the GitHub API)

## Install

```sh
pipx install --editable .   # puts `gitstat` on your PATH; code edits apply immediately
# or run without installing (from anywhere):
PYTHONPATH=/path/to/gitstat python3 -m gitstat --help
```

## Usage

```sh
cd path/to/your/repo

gitstat                                  # team overview, last 90 days
gitstat --since 30d                      # last 30 days (also 12w, 6m, 1y, or YYYY-MM-DD)
gitstat --author octocat                 # detailed report for one developer
gitstat --me                             # your own detailed report
gitstat --top 15                         # only the 15 most active people
gitstat --format html -o team.html       # interactive dashboard (single self-contained file)
gitstat --format html --author alice --author bob -o ab.html   # dashboard opened on alice, compared with bob
gitstat --format csv -o team.csv         # one row per developer, every metric
gitstat --format md --all-details        # Markdown for a wiki or a review doc
gitstat --format json                    # raw data for your own tooling

# Analyse a repo without cd-ing into it (uses the API for commits if no clone):
gitstat --repo org/api --since 2026-01-01 --until 2026-04-01
```

The first run pages through every PR updated in the window, which takes about 1 to 2 minutes for a few hundred PRs. Results are cached in `~/.cache/gitstat/`, and later runs only fetch what changed, so they take seconds. Use `--no-cache` to force a full refetch.

## What it measures

Every metric is computed per developer for the chosen window. Time metrics use wall-clock time.

**Authoring**: PRs opened, merged, closed unmerged and still open. Merge rate, PRs per week, and time between PRs. PR size in lines and files, and commits per PR. Wait for first feedback, time to merge (median and p90), and how often PRs needed changes. Comments received per PR. **Feedback → next commit** is how long after a reviewer's comment the author pushes a change. **Feedback → reply** is how long until they answer. Also counts PRs merged without approval and self-merged PRs.

**Reviewing**: reviews given, approvals, changes requested, inline and conversation comments. PRs and teammates reviewed. PRs merged for others. Review requests received, the share answered, and **review turnaround** (from the review request to the submitted review, median and p90).

**Commits**: commits, commits inside PRs (useful when PRs are squash-merged), active days, commits per week and per active day, time between commits, longest streak, lines added and deleted, commit size, and the share of commits made off-hours (weekends or outside 08:00–19:00 in the author's own timezone).

Each detailed report also includes weekly activity sparklines or charts, a heatmap of commits by weekday and hour, recent PRs, and **observations**. Observations are rule-based notes such as "PRs are 3× the team's median size" or "Best on the team for review turnaround", and each one shows the numbers behind it.

### The HTML dashboard

A single file with no external dependencies. Open it in any browser or attach it to an email. It follows the system light/dark setting and has a theme toggle.

- **Team trends:** weekly activity stacked by developer (commits, PRs opened, PRs merged or reviews), plus histograms of time to merge and wait for first feedback.
- **Compare developers:**
  - a ranked bar chart for any metric, with the team median marked
  - a scatter plot with selectable axes (e.g. volume vs. speed); skewed axes switch to a log scale
  - a "who reviews whom" matrix
  - a strip plot showing the spread of each person's PR cycle times
- **Developers:** a sortable, filterable table with weekly activity sparklines.
- **Developer detail:** pick a developer, and optionally a second one to compare side by side. It shows:
  - a percentile profile of where they sit among active peers on every metric
  - weekly trend lines against the team average
  - commit-time heatmaps
  - recent PRs
  - full metric tables

Every chart has hover tooltips. Clicking a developer in any chart or the table opens their detail.

### Team comparisons

Team medians and ranks only include developers who were active in the same area. Authoring metrics are compared among PR authors, review metrics among reviewers, and commit metrics among committers. This stops drive-by contributors from pulling every median to zero.

## Showing names instead of logins

If logins are SSO numbers or handles nobody recognises, give people display names. Names only change how people are shown. Which commits and PRs belong to whom is unchanged, and the login stays visible next to the name (e.g. "Jane Doe (@a123456)").

```sh
gitstat --print-names > ~/.gitstat.json   # a "names" block for everyone in the window, pre-filled from GitHub profiles
gitstat --fetch-names                # use GitHub profile names for anyone not listed in the config
gitstat --no-names                   # show raw logins
gitstat --author "Jane Doe"          # --author accepts a login or a display name
```

Edit the names in that file. If you already have a `.gitstat.json`, merge the `names` block into it rather than overwriting it. gitstat reads `.gitstat.json` from the current directory, then the repo given with `--path`, then your home directory, or any file passed with `--config`. It prints which file it used. A config in your home directory keeps employee names out of the repo. Names from the config always win over GitHub profile names. Set `"fetch_names": true` in the config to always fill in the rest from profiles. Profile names are cached for a week.

When profile names are fetched, commits whose author name matches exactly one GitHub profile name are credited to that account, and each match is logged. Commit authors that still aren't linked to a login are listed by `--print-names`. Map those under `aliases`.

## Configuration

Commit authors are matched to GitHub logins automatically. The tool uses emails GitHub has linked to PR commits, `@users.noreply.github.com` addresses, and a GitHub lookup for any remaining unknown emails. If someone still shows up under their name instead of their login, add an alias in `.gitstat.json` (in the working directory or `~`). You can also exclude accounts:

```json
{
  "names": {
    "a123456": "Jane Doe",
    "b654321": "Bob Roe"
  },
  "fetch_names": true,
  "aliases": {
    "vyapak@personal-laptop.local": "vyapakgoyal",
    "Jane Doe": "janedoe"
  },
  "exclude": ["release-bot", "former-contractor"]
}
```

Bot accounts (GitHub `Bot` type, `*[bot]`, `*-bot`) are excluded by default. Use `--include-bots` to keep them. Git's own `.mailmap` is also respected.

Other useful flags: `--branch`, `--all-branches`, `--api-commits`, `--exclude LOGIN`, `--page-size` (lower it if GitHub reports query timeouts).

## Reading the numbers responsibly

These metrics describe *how* work flows. They don't measure how valuable it is. Mentoring, design, incident response and hard problems that end in one small PR won't show up here, and any single number is easy to game once it becomes a target. Use trends and outliers as questions to explore, not as scores.

## Development

```sh
make            # list targets
make test       # unit tests
make install    # pipx install --editable .
make report REPO=~/src/api SINCE=30d AUTHOR=octocat
make html   REPO=~/src/api OUT=team.html
```
