# smaug-jev

*Your X bookmarks, sorted into topics you choose and written up as notes in Obsidian (or any folder), on a schedule.*

I had 443 bookmarks on X going back to 2018. I had read approximately none of them twice.

That's the bookmark problem: saving feels like learning, and it isn't. The post you saved because it explained something perfectly is gone the moment you scroll past it, buried under four hundred others with no titles, no folders, and no search worth the name.

So this pulls your bookmarks out on a schedule, decides which of your topics each one belongs to (or skips it), reads the whole thing (threads, linked articles, the text in screenshots), and writes it up as a note where you already keep your notes. Each note gets a summary, the key points, a verbatim quote or two so you remember how it *sounded*, and, when a post shares a recipe, prompt, or tool, a how-to: what they did, what they claim it gets you, and the steps to try it yourself.

It runs while you sleep. You wake up to notes.

## How it works

1. **Fetch.** [Smaug](https://github.com/alexknowshtml/smaug) pulls new bookmarks through your own X session, expands links, grabs article text, follows threads and quoted posts, and collects images.
2. **Read images.** Posts that are mostly screenshots get their images read by Claude Haiku, so a story told in four photos counts as a story.
3. **Sort.** [Jev](https://typesafe.ai), a small, fast judgment model from TypeSafe, asks one yes/no question per topic ("Is this about AI?") and the bookmark goes to the topic it scores highest on. Nothing fits? It's skipped. That's how the junk stays out.
4. **Write.** Claude Haiku writes the note. Quotes are checked word for word against the source before they're kept; a paraphrase in quotation marks never makes it into your vault.
5. **Remember.** Every processed bookmark goes in a ledger, so nothing is done twice and a note that failed gets retried next run.

## What you need

- **Node.js 22+** and **[uv](https://docs.astral.sh/uv/)**
- **An X account** you're logged in to in a browser
- **A TypeSafe API key** for Jev ([typesafe.ai](https://typesafe.ai)); a few cents covers hundreds of bookmarks
- **Claude Code**, logged in (uses your Claude plan), *or* an **Anthropic API key** (pay as you go)
- **Obsidian** if you want notes in a vault (optional; the notes are plain Markdown)

## Get started

Open `docs/onboarding.html` in your browser and plan your topics, folders, and schedule. It saves a small answers file (no secrets in it, on purpose). Then, from this folder:

```powershell
# Windows
powershell -ExecutionPolicy Bypass -File setup.ps1 --answers $HOME\Downloads\my-setup.json
```

```bash
# macOS / Linux
./setup.sh --answers ~/Downloads/my-setup.json
```

Or skip the planner and run `setup.ps1` / `./setup.sh` with no arguments; the wizard asks everything itself.

The wizard checks your tools, asks for your X cookies and API keys in hidden prompts, confirms each one actually works, finds your Obsidian vaults, installs the schedule, and offers a test drive on your five newest bookmarks (into a preview folder, so nothing lands in your vault until you've seen it).

## What it costs

Measured on my own backlog, so treat it as one data point:

- **Jev:** about 1.5 cents for roughly 400 sortings, counting every test run while I built and tuned this.
- **Claude Code:** about a quarter of a Claude Pro 5-hour usage window per 100 notes.
- **Anthropic API instead:** roughly a cent per note.

Batch size is yours to set. A big backlog clears a batch per run.

## Where your stuff goes

Your settings, your topics, and the ledger live in one data folder (`~/.smaug-jev`). Your secrets are stored where only you can read them: Windows user environment variables, or a file only your account can open on macOS and Linux. They never go in a config file, the answers file, or a log.

What leaves your computer: your X session talks to X (it's your login, used the way your browser uses it). Bookmark text goes to TypeSafe for sorting and to Anthropic for writing notes. That's it.

## Honest caveats

- **bird is frozen.** Smaug reads X through [bird](https://www.npmjs.com/package/@steipete/bird), whose author has stopped maintaining it (version 0.8.0 is the last). It works today. If X changes its internals, it may stop, and there's no upstream to fix it.
- **This uses your X session cookies** for automated reading, which X's terms don't love. It only reads your own bookmarks, at a modest pace, but it's your account and your call.
- **Windows is the tested path.** The macOS (launchd) and Linux (systemd) schedulers are written to spec and haven't had their first real run yet. If you're the first, tell me how it went.
- **Video is invisible.** A post whose substance is a video gets a short, honest note that says so, plus the link.
- **Sorting is good, not perfect.** On my reviewed sample, Jev's call matched mine 14 times out of 15. Your topics, your wording, your mileage.

## When something breaks

`setup status` shows what's configured and points at the newest log. [docs/RUNBOOK.md](docs/RUNBOOK.md) covers the rest: expired X logins, changing topics, running it right now, costs, and uninstalling.

## Credits

Built on [Smaug](https://github.com/alexknowshtml/smaug) by Alex Hillman (MIT; vendored here with changes, listed in `smaug/CHANGES-smaug-jev.md`), [bird](https://www.npmjs.com/package/@steipete/bird) by Peter Steinberger, [Jev](https://typesafe.ai) by TypeSafe, Mozilla's [Readability](https://github.com/mozilla/readability), and Claude.
