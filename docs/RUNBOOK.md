# Runbook

Everything you might need after setup, roughly in the order you'll need it. New here? Start with [Getting started](../README.md#getting-started-on-windows) in the README.

> [!NOTE]
> **About the commands on this page.** They assume setup installed the `scatha` command (it offers to, at the end). If you skipped that, open a terminal in the Scatha folder and swap in the long form:
>
> - `scatha setup ...` becomes `powershell -ExecutionPolicy Bypass -File setup.ps1 ...` on Windows, or `./setup.sh ...` on macOS and Linux.
> - Any other `scatha ...` becomes `uv run --project router scatha ...`.

## Contents

1. [Setup, and running it again](#1-setup-and-running-it-again)
2. [What a run does](#2-what-a-run-does)
3. [Checking in, and running it now](#3-checking-in-and-running-it-now)
4. [When nothing fits: Unsorted, the desk, and discovery](#4-when-nothing-fits-unsorted-the-desk-and-discovery)
5. [Tuning your topics](#5-tuning-your-topics)
6. [Where things live](#6-where-things-live)
7. [When something breaks](#7-when-something-breaks)
8. [Costs and limits](#8-costs-and-limits)
9. [Moving, pausing, uninstalling](#9-moving-pausing-uninstalling)
10. [For maintainers](#10-for-maintainers)

## 1. Setup, and running it again

The setup wizard has seven steps: tools, X login, Jev, Haiku, topics and folders, schedule, and a test drive. You type each secret into a hidden prompt, and setup checks it against the real service before saving it.

You can run `scatha setup` again any time. It checks every step (the logins live, which takes a few seconds) and shows what's already set. Then:

- **Something missing or broken?** It goes straight there. A setup you stopped partway picks up where you left off, and an expired login gets fixed without redoing the rest.
- **Everything working?** It offers to change any one step.

To jump to one step directly: `scatha setup x-login`, `scatha setup topics`, or `scatha setup schedule`. And `scatha setup status` checks everything without changing anything.

The test drive runs the scheduled script itself, in a fresh window, so it sees exactly what the scheduler will. On Linux with systemd, it runs through systemd, so it gets the same bare PATH the timer will.

### On a server with no screen (Linux over SSH)

Everything above works over SSH. A few things go differently without a browser or a desktop:

- **Getting the code there.** `git clone` the repo, or copy the folder over (`scp -r`).
- **Claude Code login.** Run `claude` once. With no browser, it prints a login link; open it on any other machine and paste back the code it gives you.
- **X cookies.** Copy `auth_token` and `ct0` from a browser on any machine where you're logged in to X (the wizard says where to find them), then paste them into the wizard on the server.
- **Folders.** There's probably no Obsidian on a server, so the wizard asks for a plain folder. Point it at a synced folder if you want the notes somewhere else.
- **Keep the timer alive after you log out.** systemd stops your timers when your last session ends. Run `loginctl enable-linger $USER` once (some systems need `sudo`). The wizard reminds you if lingering is off.
- **"Failed to connect to bus."** Some SSH logins don't get a systemd user session. Enabling lingering and logging in again usually fixes it. Until then, setup prints a crontab line to add instead.
- **Node from nvm.** The schedule doesn't load your shell profile, so it may not find a Node or Claude Code that only nvm knows about. The test drive catches this. Install Node system-wide, or tell me how it failed.

## 2. What a run does

On schedule (the default is Saturdays at 00:01), a run:

1. **Checks in first.** It makes sure your X login, Jev, and the note writer all answer. If one doesn't, it stops before fetching and says which one, in the log and in a phone ping if you set up ntfy.
2. **Fetches** up to *batch size* bookmarks you haven't processed yet, newest first. Threads, quoted posts, linked articles, and images come along.
3. **Reads the images** on posts that are mostly pictures.
4. **Sorts.** Jev scores each bookmark against each topic. Below the keep line on every topic means Unsorted.
5. **Writes a note** per kept bookmark, in that topic's folder. A bookmark that already has a note there (same `tweet_id`) isn't written twice. Unsorted bookmarks get a light note instead (see [section 4](#4-when-nothing-fits-unsorted-the-desk-and-discovery)).
6. **Records** everything it handled in the ledger. A note that failed stays out of the ledger, so the next run tries it again.
7. **Reports.** It logs a one-line summary (and pings it, if ntfy is set up), adds a line to `work/runs.jsonl`, and rewrites the status note in your vault.

If a run is interrupted after fetching, the next run finishes that batch before fetching a new one.

## 3. Checking in, and running it now

Type `scatha`. It shows the last run, what's in Unsorted, what failed and will be retried, what's still queued, and what you can do about any of it right now.

Opening the report (or the sorting desk) counts as having looked. Until you do, every other `scatha` command starts with a one-line "since you last looked" reminder. (A run at midnight is easy to forget about, so Scatha reminds you.)

| Command | What it does |
|---|---|
| `scatha` | The report above |
| `scatha review` | Opens the sorting desk: sort Unsorted bookmarks, check Jev's close calls ([section 4](#the-sorting-desk)) |
| `scatha discover` | Looks for a topic your Unsorted bookmarks share ([section 4](#find-the-missing-topic-scatha-discover)) |
| `scatha run` | Runs an import now (options below) |
| `scatha setup` | The setup wizard ([section 1](#1-setup-and-running-it-again)) |
| `scatha uninstall` | Lists what Scatha put on your computer and removes only what you pick ([section 9](#9-moving-pausing-uninstalling)) |

### Don't want to wait for the schedule?

Type `scatha run`. Useful options:

| Option | What it does |
|---|---|
| `--batch 25` | Takes 25 this time, instead of your batch size |
| `--preview` | Writes notes to `~/.scatha/work/notes-preview` instead of your folders. Nothing is recorded, so the same bookmarks come up again next time |
| `--force` | Runs even if the every-other-week schedule says it's an off week |

The log lands in `~/.scatha/work/logs/` either way.

To run it exactly the way the scheduler does (a fresh window, the scheduler's settings), run the scheduled script itself: `powershell -ExecutionPolicy Bypass -File "$HOME\.scatha\run.ps1"` on Windows, or `~/.scatha/run.sh` on macOS and Linux. The same options work there.

## 4. When nothing fits: Unsorted, the desk, and discovery

### Unsorted

A bookmark that scores below the keep line on every topic goes to your **Unsorted** folder as a light note: the post, its links, the text in its images, the full text, and Jev's scores. No model writes it, so it costs nothing.

Don't want light notes? Set `"unsorted": {"mode": "off"}` in `config.json`, and Scatha keeps only the ledger line.

### The sorting desk

Type `scatha review`. A page opens in your browser. It runs on your computer, and nobody else can reach it.

The first time, the desk re-scores every bookmark Scatha has processed against your current topics. That's Jev only: a few cents, about 2,300 tokens a bookmark. After that, it only scores new bookmarks.

The desk opens on two kinds of bookmarks:

- **Unsorted ones,** waiting for a topic.
- **Close calls,** where two topics scored almost the same, or where the best score sits near the keep line.

For each one, click the topic it belongs in, **Don't Keep**, or **Not Sure**. **Don't Keep** means you don't want it; if it's worth keeping but no topic fits, skip it, so `scatha discover` can still find it a topic. Each click is saved right away (in `tuning/verdicts.json`); anything you don't click doesn't count. Keyboard: `j`/`k` to move, `1`-`9` for a topic, `0` for **Don't Keep**, `u` for **Not Sure**.

When you press **Done**, the desk offers to write full notes for anything you sorted out of Unsorted, and to remove the light notes you marked **Don't Keep**.

Posts that aren't in English are shown in English: the note's translation if there is one, otherwise one Haiku translation, saved for next time. The original is a click away.

### "Not worth keeping": deleting from Unsorted

Unsorted is Scatha's folder, so deleting a light note there counts as an answer: *not worth keeping*. Scatha notices the next time it looks (after each run, and whenever you type `scatha`), and the report counts these answers.

- **Moving** a light note out of Unsorted doesn't count. Maybe you filed it yourself.
- **Deleting a full note** from a topic folder doesn't count either. People prune their notes for all kinds of reasons.
- **More than 10 gone at once** looks like a folder that was emptied or moved, so Scatha asks first. If a night run sees it, it waits and asks the next time you type `scatha`.
- **Changed your mind?** Give the bookmark a topic on the desk. Done then writes its full note.

### Find the missing topic: `scatha discover`

Sometimes Unsorted piles up because you're missing a topic. Once 15 new bookmarks have gone to Unsorted since you last checked, the report and the run summary suggest this.

Type `scatha discover`. It costs a few cents (one Haiku call, plus Jev scoring each idea). It looks at bookmarks that fit nothing well and that you haven't answered on the desk, then proposes up to three topics they share. For each idea, it shows:

- how many bookmarks it would rescue from Unsorted,
- how many already-filed notes also fit it (and which of your topics they're filed under),
- and where it disagrees with answers you gave on the desk.

Ideas that would rescue fewer than 5 bookmarks are set aside.

For each idea that holds up, pick **Add**, **Not for Me** (never suggested again), or **Not Now**. If you add it:

1. You name it and pick its folder: a new folder in one of your vaults, a folder another topic already uses, or any folder you type in. (A folder outside every Obsidian vault gets a warning, since Obsidian won't show those notes.)
2. Scatha adds it to `topics.yaml` (keeping a copy of the old file in `tuning/`) and updates every note's topic list.
3. It offers to write full notes for the bookmarks it rescues from Unsorted (what that costs: [section 8](#8-costs-and-limits)).

Filed notes never move. If some fit the new topic better, the report says so, and their topic lists include it.

Two free variations: `scatha discover --pool` just lists the bookmarks it would look at, and `scatha discover --last` lets you decide again on the last run's ideas.

## 5. Tuning your topics

`topics.yaml` is plain words, and Jev reads them literally. Each topic has three fields:

- `question`: one yes/no question ("Is this item about sports?")
- `yes_means`: what counts, with concrete kinds of posts
- `no_means`: what doesn't, *especially* the near-misses that belong to your other topics

When a bookmark lands in the wrong place, open that run's `work/runs/<date>/routes.md` to see every score, then fix the wording. Some patterns that work:

- **Name the near-miss.** If job-hunting advice keeps landing in Life hacks, add "Advice about careers or job searching belongs to knowledge work" to Life hacks' `no_means`.
- **Teach the jargon.** Jev doesn't know your field's slang. "Terms like skills, MCP, or subagents refer to AI tooling" fixed a whole class of misses for AI.
- **Keep it to one judgment per topic.** "Is this about AI *and* useful?" is two questions. Split it, or drop one.
- **Move the keep line.** `thresholds.route` (default 0.3) is how sure Jev must be before a bookmark is kept. Raise it to keep less.

After you edit `topics.yaml`, run `scatha review --rebuild` so the desk re-scores with the new wording. Your desk answers are the test any rewording has to pass: `uv run --project router python router/regression/score.py <routes.json> ~/.scatha/tuning/verdicts.json` shows how many Jev now gets right.

`scatha setup topics` rebuilds the file from scratch (and can re-draft definitions with Haiku). Editing the YAML by hand is usually faster.

## 6. Where things live

```
~/.scatha/                (or wherever SCATHA_HOME points)
├─ config.json               batch size, note writer, schedule, tool paths
├─ topics.yaml               your topics: question, what counts, what doesn't, folder
├─ run.ps1 / run.sh          what the scheduler calls (rewritten by `scatha setup schedule`)
├─ secrets.env               macOS/Linux only, readable only by you
├─ tuning/
│  ├─ verdicts.json          your answers on the sorting desk
│  ├─ not-for-me.json        topic ideas you turned down
│  ├─ discovery/             each discovery run
│  ├─ desk/                  the desk's scores
│  └─ topics_before-*.yaml   copies of topics.yaml from before each added topic
└─ work/
   ├─ ingested.md            the ledger: one line per processed bookmark
   ├─ runs.jsonl             one line per run, for the report
   ├─ logs/                  one log per run
   ├─ runs/<date>/           what each run saw: scores (routes.md) and the raw batch
   └─ notes-preview/         where --preview and the test drive write
```

### Secrets

- **Windows:** in Windows Credential Manager, under `scatha`: `TWITTER_AUTH_TOKEN`, `TWITTER_CT0`, `TYPESAFE_API_KEY`, and `ANTHROPIC_API_KEY` if you use the API. If Credential Manager isn't available, setup falls back to user environment variables and says so.
- **macOS and Linux:** in `secrets.env` in the data folder, readable only by your account.
- **Where's mine?** `scatha setup status` shows where each one is kept.

Credential Manager hands your secrets only to programs that ask for them by name. User environment variables, where earlier versions kept them, go to every program you start. Credential Manager narrows who sees them; it doesn't lock them. (Any program running as you *can* still ask.)

Still have secrets in environment variables from an earlier version? `scatha setup secrets` moves them. It asks about each one, because another program of yours may read the same variable. The plain `AUTH_TOKEN` and `CT0` that older setups used get copied, and removed only if you say so, since bird and other X tools read those names.

### What's in a note

Each note's front matter carries `source`, `tweet_id`, `author`, `posted`, `captured`, `bucket`, `bucket_score`, `topics`, `topic_scores`, `tags`, and `words`, so Obsidian's Properties and Bases can sort and filter them.

**Topics overlap.** A bookmark is often about more than one of your topics, and the line between two topics is rarely sharp. The folder holds the strongest topic. `topics` lists every topic Jev scored at or above the keep line (the folder's topic first), so a Bases view or a search for a topic also finds notes filed elsewhere. `topic_scores` shows every score, strongest first. To list fewer extra topics, add `also: 0.5` (or any number) under `thresholds` in `topics.yaml`.

Notes written before these properties existed, or before you changed your topics, catch up with this (no model calls; a dry run unless you add `--apply`; safe to repeat):

```
uv run --project router python -m jev_router.notes topics --scores <routes.json or tuning/desk/deck.json> --taxonomy <topics.yaml> --notes <folder> ... [--apply]
```

**GitHub repos.** A note that links a GitHub repo also carries its vital signs: `repo`, `repo_stars`, `repo_pushed`, `repo_archived`, `repo_last_release` (when it has releases), and `repo_checked`, the date they were true. Each repo's line under Links shows its own stars and last push. The front matter only gets them when all the linked repos share one owner (one project, maybe in parts). A list of repos from several owners isn't about any one of them. Old notes catch up with:

```
uv run --project router python -m jev_router.notes repo-vitals --notes <folder> ... [--apply]
```

GitHub allows 60 lookups an hour without a token, two per repo. Set `GITHUB_TOKEN` for more.

## 7. When something breaks

Start with `scatha setup status`. It checks every step, tests the three logins live, and shows the newest log. Running `scatha setup` does the same check, then fixes whatever failed.

| What you see | Likely cause | What to do |
|---|---|---|
| Log or ping says "Bookmark import stopped before fetching" | A login expired, or Claude Code moved or logged out. The message names which | `scatha setup` goes straight to the broken step (`scatha setup x-login` for X cookies alone) |
| Log shows `falling back to bird`, or the fetch fails | X changed something the built-in client depends on | If the backup (bird) carried the run, nothing is lost yet. Either way, the fix goes in `smaug/vendor/bird/` (see its README) |
| The `X client:` line says `N rate-limited` | X asked Scatha to slow down | Nothing. Those bookmarks are retried next run. A missing author bio is just left out |
| `claude -p failed` in the log | Claude Code logged out, or you hit your usage window | Run `claude` once to log back in. Failed notes are retried next run |
| Some notes failed with `content filtering policy` | Claude declined to read a particular post or image | Nothing. That post is filed from its text alone, or retried |
| Nothing ran on schedule (Windows) | You weren't logged in, or the PC was off | It runs at your next login (the task catches up). Logged in but locked is fine |
| Nothing ran on schedule (macOS) | The Mac was off at the time | launchd runs missed jobs on wake, but not after a full shutdown. Use `scatha run` |
| "another run holds the lock" | A run is going, or one crashed in the last 6 hours | Wait, or delete `work/.run.lock` if nothing's running |
| Jev errors (401/403) | Your TypeSafe key was revoked or ran out of credit | Run `scatha setup`. It finds the rejected key and asks for a new one |
| A topic folder stays empty | Its definition is too narrow, or you don't bookmark much of it | Check the scores in `routes.md`, and widen `yes_means` |
| Notes landed but Obsidian doesn't show them | The topic's folder isn't inside an Obsidian vault | Move the folder into a vault, then update that topic's `destination` in `topics.yaml` |
| `scatha setup status` says some secrets are plain environment variables | They were saved by an earlier version | `scatha setup secrets` ([section 6](#secrets)) |

## 8. Costs and limits

- **Jev** charges per input token: pennies per hundred bookmarks.
- **Claude Code** draws on your plan's usage window. One Pro user saw about a quarter of a 5-hour window per 100 notes. Long articles and image-heavy posts cost more.
- **Anthropic API** is roughly a cent per note with Haiku.
- **X** gets one request per page of your bookmark list, plus a few per bookmark (threads, quotes, profiles). A batch of 100 takes a few minutes. Keep batches modest; it's your account.
- **Your newest 1,000 bookmarks.** Each run looks at your newest 1,000. If you have more, the older ones won't come through (yet).
- **One X account.** Scatha reads one account's bookmarks per computer. Support for several is on the roadmap but not built yet. Until then, a second install would share the first one's saved login and run on its own schedule, so don't set one up.

Batch size lives in `config.json` (or `scatha setup schedule`). For a big backlog, a larger batch clears it sooner, at the cost of a bigger bite out of one usage window.

## 9. Moving, pausing, uninstalling

- **Pause:** `scatha setup unschedule`. Bring it back with `scatha setup schedule`.
- **Move the Scatha folder** (the program): move it, then open a terminal in its new spot. The `scatha` command still points at the old spot, so use the long form here: `powershell -ExecutionPolicy Bypass -File setup.ps1 schedule` on Windows, or `./setup.sh schedule` on macOS and Linux. That points the schedule at the new spot and puts the `scatha` command back.
- **Move the data folder** (`~/.scatha`): set `SCATHA_HOME` to the new location (as a user environment variable), move the folder, then run `scatha setup schedule`.

### Uninstalling

Type `scatha uninstall`. It lists everything Scatha put on your computer:

- the schedule (and any a test install left behind),
- the data folder,
- each saved secret,
- the status note,
- Smaug's packages,
- and the `scatha` command.

Each item says what it's for and what else removing it touches. For example, `AUTH_TOKEN` and `CT0` are read by bird and other X tools too.

**Nothing is removed unless you pick it,** by number or by name: `scatha uninstall --remove schedule data`. Add `--yes` to skip the confirmation.

- **Before deleting the data folder,** it offers to keep a copy of your topics, settings, and desk answers.
- **Your notes are listed but never offered.** They're plain Markdown, and they work without Scatha.
- **A wrong `SCATHA_HOME` can't take a vault with it.** Uninstall refuses to delete a data folder that holds anything Scatha didn't put there. It also refuses when it finds a file it can't be sure it made, even one of its own it doesn't recognize yet. Then the folder stays, the list shows the files it found, and you can delete the folder by hand once you've checked nothing in it is yours.

**What uninstalling can't do for you:**

- **Log you out of X.** The cookies keep working until you log out in your browser.
- **Revoke your TypeSafe or Anthropic key.** Do that on their sites.
- **Unsubscribe your phone from ntfy.** Do that in the ntfy app.
- **Delete the Scatha folder,** which holds the program and its Python environment. Delete it by hand once you're done.

## 10. For maintainers

- **Code:** `router/` (Python: sorting, notes, runner, wizard, scheduler, desk, discovery, uninstall) and `smaug/` (Node: fetching, vendored from upstream with the changes listed in `smaug/CHANGES-scatha.md`).
- **Tests:** `uv run --project router pytest router/tests` (no network) and, in `smaug/`, `npm test` (two live tests need `AUTH_TOKEN` and `CT0` set; the rest are offline).
- **Smaug changes** go in their own commits, touching only `smaug/`, with messages starting `smaug:`, so each one can be offered upstream as a clean PR.
- **Renaming:** the name lives in `router/src/jev_router/app.py` (`APP_NAME`, `HOME_DIRNAME`, `HOME_ENV`, `TASK_NAME`), plus prose in this file, the README, the planner page, and the setup scripts. After renaming, existing installs need `scatha setup unschedule` with the old version, then `scatha setup schedule` with the new one.
