# Runbook

Everything you might need after setup, roughly in the order you'll need it. The commands assume you're in the project folder; on Windows, `setup` means `powershell -ExecutionPolicy Bypass -File setup.ps1`, and on macOS/Linux it means `./setup.sh`.

## Contents

1. [Install](#1-install)
2. [What a run does](#2-what-a-run-does)
3. [Run it now](#3-run-it-now)
4. [Where things live](#4-where-things-live)
5. [Tuning your topics](#5-tuning-your-topics)
6. [When something breaks](#6-when-something-breaks)
7. [Costs and limits](#7-costs-and-limits)
8. [Moving, pausing, uninstalling](#8-moving-pausing-uninstalling)
9. [For maintainers](#9-for-maintainers)

## 1. Install

1. Install Node.js 22+ (nodejs.org) and uv (`setup` prints the one-line installer if it's missing).
2. Get a TypeSafe API key at typesafe.ai.
3. Install Claude Code and log in once by running `claude`. (Or have an Anthropic API key ready instead.)
4. Optional: open `docs/onboarding.html`, plan your topics and schedule, and download `my-setup.json`.
5. Run `setup` (add `--answers path/to/my-setup.json` if you made one).

The wizard runs seven steps: tools, X login, Jev, Haiku, topics and folders, schedule, and a test drive. Each secret is typed into a hidden prompt and checked against the real service before it's stored. Run `setup` again any time. It checks every step (the logins live, which takes a few seconds), shows what's already set, and goes straight to whatever is missing or broken: a setup you stopped partway resumes where you left off, and an expired login gets fixed without redoing the rest. If everything works, it offers to change any one step. The test drive runs the scheduled script itself, in a fresh shell, so it proves what the scheduler will see. On Linux with systemd, it runs through systemd, so it gets the same bare PATH the timer will.

### On a server with no screen (Linux over SSH)

Everything above works over SSH. A few things go differently without a browser or a desktop session:

- **Getting the code there.** `git clone` the repo, or copy the folder over (`scp -r`, or a `git bundle` file if the repo is private to you).
- **Claude Code login.** Run `claude` once. With no browser, it prints a login link; open it on any other machine and paste back the code it gives you.
- **X cookies.** Copy `auth_token` and `ct0` from a browser on any machine where you're logged in to X (the wizard says where to find them), then paste them into the wizard on the server.
- **Folders.** There's probably no Obsidian on a server, so the wizard asks for a plain folder. Point it at a synced folder if you want the notes elsewhere.
- **Keep the timer alive after you log out.** systemd stops a user's timers when their last session ends. Run `loginctl enable-linger $USER` once (some systems need `sudo`). The wizard reminds you if lingering is off.
- **"Failed to connect to bus."** Some SSH logins don't get a systemd user session. Enabling lingering and logging in again usually fixes it; until then, setup prints a crontab line to add instead.
- **Node from nvm.** The schedule doesn't load your shell profile, so a Node or Claude Code that only nvm knows about may not be found. The test drive will catch this; install Node system-wide, or tell me how it failed.

## 2. What a run does

On schedule (default: Saturdays at 00:01), the runner:

1. Checks that your X login, Jev, and the note writer all answer. If one doesn't, it stops before fetching and tells you which: in the log, and in a phone ping if you set up ntfy.
2. Fetches up to *batch size* bookmarks you haven't processed yet, newest first. Threads, quoted posts, linked articles, and images come along.
3. Reads the images on posts that are mostly pictures.
4. Asks Jev which topic each bookmark fits. Below the threshold on every topic means unsorted.
5. Writes a note per kept bookmark into that topic's folder. A bookmark that already has a note (same `tweet_id` in the folder) isn't written twice. Unsorted bookmarks become light notes in your Unsorted folder: the post, its links, the text in its images, the full text and Jev's scores, with no model call. Set `"unsorted": {"mode": "off"}` in `config.json` to keep only the ledger line instead.
6. Records everything it handled in the ledger. A note that failed stays out, so the next run retries it.
7. Logs a one-line summary (and pings it, if ntfy is set), adds a line to `work/runs.jsonl`, and rewrites the status note in your vault (`scatha.md` beside the Unsorted folder, or wherever `status_note` in `config.json` says).

If a run is interrupted after fetching, the next run resumes that batch instead of fetching a new one.

## 3. See what happened, run it now

Type `scatha`. It shows the last run, what's in Unsorted, what failed and will retry, what's still queued, and the levers that apply right now. Opening it (or the desk) counts as having looked: until then, every other `scatha` command starts with a one-line "since you last looked" reminder.

| Command | Does |
|---|---|
| `scatha` | The report above |
| `scatha review` | The sorting desk: sort Unsorted bookmarks, check Jev's close calls. When you press Done, it offers to write full notes for anything you sorted out of Unsorted and to remove the ones you marked Don't keep |
| `scatha run` | A run now (same options as below) |
| `scatha setup` | The setup wizard (`scatha setup topics`, `scatha setup x-login`, ...) |

The wizard installs the command (`uv tool install --editable <repo>/router`); if you skipped that, `uv run --project <repo>/router scatha` does the same.

Don't want to wait for Saturday?

```powershell
# Windows
& "$HOME\.scatha\run.ps1"
```

```bash
# macOS / Linux
~/.scatha/run.sh
```

Useful options (pass them after the script):

| Option | Does |
|---|---|
| `--batch 25` | Take 25 this time instead of your batch size |
| `--preview` | Write notes to `~/.scatha/work/notes-preview` instead of your folders; nothing is recorded |
| `--force` | Run even if the every-other-week guard says it's an off week |

The log lands in `~/.scatha/work/logs/` either way.

## 4. Where things live

```
~/.scatha/                (or wherever SCATHA_HOME points)
├─ config.json               batch size, note writer, schedule, tool paths
├─ topics.yaml               your topics: question, what counts, what doesn't, folder
├─ run.ps1 / run.sh          what the scheduler calls (regenerated by `setup schedule`)
├─ secrets.env               macOS/Linux only, readable only by you
└─ work/
   ├─ ingested.md            the ledger: one line per processed bookmark
   ├─ logs/                  one log per run
   ├─ runs/<date>/           what each run saw: routing table (routes.md) and the raw batch
   └─ notes-preview/         where --preview and the test drive write
```

On Windows the secrets are user environment variables: `TWITTER_AUTH_TOKEN`, `TWITTER_CT0`, `TYPESAFE_API_KEY`, and `ANTHROPIC_API_KEY` if you use the API.

Each note's front matter carries `source`, `tweet_id`, `author`, `posted`, `captured`, `bucket`, `bucket_score`, `topics`, `topic_scores`, `tags`, and `words`, so Obsidian's Properties and Bases can sort and filter them.

A bookmark is often about more than one of your topics, and the line between two topics is rarely sharp. The folder holds the strongest one; `topics` lists every topic Jev scored at or above the keep line (the folder's topic first), so a Bases view or search for a topic also finds notes filed elsewhere. `topic_scores` shows every score, strongest first. To list fewer extra topics, add `also: 0.5` (or any number) under `thresholds` in `topics.yaml`. Notes written before these properties existed, or after you change topics, catch up with `uv run --project router python -m jev_router.notes topics --scores <routes.json or tuning/desk/deck.json> --taxonomy <topics.yaml> --notes <folder> ... [--apply]` (no model calls; dry run without `--apply`; safe to repeat).

A note that links a GitHub repo also carries that repo's vital signs: `repo`, `repo_stars`, `repo_pushed`, `repo_archived`, `repo_last_release` (when it has releases), and `repo_checked`, the date they were true. Each repo's line under Links shows its own stars and last push. The front matter gets them only when the linked repos share one owner (one project, maybe in parts; the first repo stands for it); a list of repos from several owners isn't about any one of them, so it gets none. Old notes can catch up with `uv run --project router python -m jev_router.notes repo-vitals --notes <folder> ... [--apply]` (dry run without `--apply`; safe to repeat). GitHub allows 60 lookups an hour without a token, two per repo; set `GITHUB_TOKEN` for more.

## 5. Tuning your topics

`topics.yaml` is plain words, and Jev reads them literally. Three fields per topic:

- `question`: one yes/no question ("Is this item about sports?")
- `yes_means`: what counts, with concrete kinds of posts
- `no_means`: what doesn't, *especially* the near-misses that belong to your other topics

**The sorting desk shows you where Jev is torn.** Run `scatha review`. The first time, it re-scores every bookmark scatha has processed against your current topics (Jev only: a few cents, about 2,300 tokens a bookmark), then opens a page in your browser. The page runs on your machine and nobody else can reach it. It opens on the close calls: bookmarks where two topics scored almost the same, or where the best score sits near the keep/drop line. For each one, click the topic it belongs in, **Don't keep**, or **Not sure**. Each click is saved right away to `tuning/verdicts.json` in your data folder; anything you don't click doesn't count. Posts that aren't in English are shown in English (the note's translation if there is a note, otherwise one Haiku translation, kept for next time), with the original a click away. Keys: `j`/`k` to move, `1`-`9` for a topic, `0` don't keep, `u` not sure. After you edit `topics.yaml`, run `scatha review --rebuild` to re-score. Otherwise the desk keeps its scores and only scores bookmarks processed since. Your answers are the test any rewording has to pass: `uv run --project router python router/regression/score.py <routes.json> ~/.scatha/tuning/verdicts.json`.

When a bookmark lands in the wrong place, open that run's `work/runs/<date>/routes.md` to see every score, then fix the wording. Some patterns that work:

- **Name the near-miss.** If job-hunting advice keeps landing in Life hacks, add "Advice about careers or job searching belongs to knowledge work" to Life hacks' `no_means`.
- **Teach the jargon.** Jev doesn't know your field's slang. "Terms like skills, MCP, or subagents refer to AI tooling" fixed a whole class of misses for AI.
- **Keep it to one judgment per topic.** "Is this about AI *and* useful?" is two questions; split it or drop one.
- **Threshold.** `thresholds.route` (default 0.3) is how sure Jev must be before a bookmark is kept. Raise it to keep less.

`setup topics` rebuilds the file from scratch (and can re-draft definitions with Haiku). Editing the YAML by hand is usually faster.

## 6. When something breaks

Start with `setup status`: it checks every step, the three logins live, and shows the newest log. Running `setup` itself does the same check and then fixes whatever failed.

| Symptom | Likely cause | Fix |
|---|---|---|
| Log or ping says "Bookmark import stopped before fetching" | A login expired, or Claude Code moved or logged out; the message names which | `setup`, which goes straight to the broken step (`setup x-login` for X cookies alone) |
| Log shows `falling back to bird`, or the fetch fails | X changed something the built-in client depends on | If the backup bird carried the run, nothing is lost yet. Either way, the fix goes in `smaug/vendor/bird/` (see its README) |
| `claude -p failed` in the log | Claude Code logged out, or you hit your usage window | Run `claude` once to log back in; failed notes retry next run |
| Some notes failed, `content filtering policy` | Claude declined to read a particular post or image | Nothing to do; that post is filed from its text alone or retried |
| Nothing ran on schedule (Windows) | You weren't logged in, or the PC was off | It runs at your next login (the task catches up). Logged-in-but-locked is fine |
| Nothing ran on schedule (macOS) | The Mac was off at the time | launchd runs missed jobs on wake, not after a full shutdown; use `run.sh` |
| "another run holds the lock" | A run is going, or one crashed in the last 6 hours | Wait, or delete `work/.run.lock` if nothing's running |
| Jev errors (401/403) | TypeSafe key revoked or out of credit | Re-run `setup`; it finds the rejected key and asks for a new one |
| A topic folder stays empty | Its definition is too narrow, or you just don't bookmark that much of it | Check scores in `routes.md`; widen `yes_means` |

## 7. Costs and limits

- **Jev** charges per input token; pennies per hundred bookmarks.
- **Claude Code** draws on your plan's usage window. One Pro user saw about a quarter of a 5-hour window per 100 notes; long articles and image-heavy posts cost more.
- **Anthropic API** is roughly a cent per note with Haiku.
- **X** gets one request per bookmark list page plus a few per bookmark (threads, quotes, profiles). A batch of 100 takes a few minutes. Keep batches modest; it's your account.
- **One X account.** scatha reads one account's bookmarks per computer. Support for several accounts is on the roadmap but not built yet; until then, a second install would share the first one's saved login (on Windows) and run on its own schedule, so don't set one up.

Batch size lives in `config.json` (or `setup schedule`). For a big backlog, a larger batch clears it sooner at the cost of a bigger bite out of one usage window.

## 8. Moving, pausing, uninstalling

- **Pause:** `setup unschedule`. Bring it back with `setup schedule`.
- **Move the data folder:** set `SCATHA_HOME` to the new location (user environment variable), move the folder, then `setup schedule` to rewrite the scheduled script.
- **Uninstall:** `setup unschedule`, delete `~/.scatha`, and remove the secrets (Windows: delete the four user environment variables). Your notes stay where they are; they're just Markdown.

## 9. For maintainers

- Code: `router/` (Python: sorting, notes, runner, wizard, scheduler) and `smaug/` (Node: fetching, vendored from upstream with the changes in `smaug/CHANGES-scatha.md`).
- Tests: `uv run --project router pytest router/tests` (no network; sorting policy, quote checking, config, scheduler formats).
- **Renaming:** the name lives in `router/src/jev_router/app.py` (`APP_NAME`, `HOME_DIRNAME`, `HOME_ENV`, `TASK_NAME`) plus prose in this file, the README, the planner page, and the setup scripts. After renaming, existing installs need `setup unschedule` with the old version, then `setup schedule` with the new one.
