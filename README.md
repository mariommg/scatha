

# scatha

<p align="center">
  <img src="assets/scatha.png" alt="A white dragon standing atop a lightly sketched hoard" width="100%">
</p>

***Your X bookmarks, sorted into topics you choose and written up as notes in Obsidian (or any folder), on a schedule.***

I had 443 bookmarks on X going back to 2018. I had read approximately none of them twice.

That's the bookmark problem: saving feels like learning, and it isn't. The post you saved because it explained something perfectly is gone the moment you scroll past it, buried under four hundred others with no titles, no folders, and a search box that never seems to find it.

**Enter Scatha.** What Scatha does is pull your bookmarks out on a schedule and decide which of your topics each one belongs to. Then it reads the whole thing (threads, linked articles, the text in screenshots) and writes it up as a note where you already keep your notes (I use it with [Obsidian](https://obsidian.md/)). Each note gets:

- a summary and the key points,
- a verbatim quote or two, so you remember what it *sounded* like,
- and, when a post shares a recipe, prompt, or tool, a how-to: what they did, what they claim it gets you, and the steps to try it yourself.

It runs while you sleep. You wake up to notes. You can read those notes yourself, or process them further using your preferred LLM.

## How it works

1. **Fetch.** [Smaug](https://github.com/alexknowshtml/smaug) pulls new bookmarks through your own X session. It expands links, grabs article text, follows threads and quoted posts, and collects images.
2. **Read images.** When a post is mostly screenshots, Claude Haiku reads the images, so a story told in four photos counts as a story.
3. **Sort.** [Jev](https://typesafe.ai), a small, fast judgment model from TypeSafe, asks one yes/no question per topic ("Is this about some-topic?"). The bookmark goes to the topic it scores highest on.
4. **Write.** Claude Haiku writes the note. Haiku picks the quotes, but the code copies them out of the source: whole sentences, nothing cut out, so a quoted half-sentence can't lose the "I don't think" in front of it. If Haiku can't point to a quote in the source, the quote is dropped.
5. **Remember.** Every bookmark goes in a ledger, so nothing is done twice, and a note that failed gets another try next run.
6. **Tell you.** Scatha leaves a status note in your vault after it runs (handy if you run Scatha at odd hours). Type `scatha` to see what happened and what you can do about it.

### When nothing fits

A bookmark that fits none of your topics isn't thrown away. It goes to an **Unsorted** folder as a light note: the post and its links, no summary, no cost.

From there, it's your call:

- **Sort it** on the sorting desk (`scatha review`), and Scatha writes the full note.
- **Toss it.** Mark it "Don't Keep" on the desk, or just delete the note. Unsorted is Scatha's folder, so a deleted note counts as your answer. (Moving one somewhere else doesn't.)
- **Find the missing topic.** When Unsorted piles up, `scatha discover` looks for a topic those bookmarks share that you haven't named yet. That's how I ended up with a Humor folder, and an R one. (In hindsight, 2018-me bookmarked a *lot* of R.)

## What you need

- **An X account** you're logged in to in a browser. One account per computer for now; support for several is on the roadmap but not built yet.
- **A TypeSafe API key** for Jev ([typesafe.ai](https://typesafe.ai)). A few cents covers hundreds of bookmarks.
- **Claude Code**, logged in (uses your Claude plan), *or* an **Anthropic API key** (pay as you go). Coming soon: ChatGPT support.
- **Obsidian** if you want notes in a vault (optional; the notes are plain Markdown, so they're pretty versatile)
- **Two free tools**, Node.js and uv. The steps below show you how to install them.

## Getting started on Windows

Scatha doesn't come with an installer you can double-click. Instead, you'll copy a few lines into a window and answer some questions. It takes about 20 minutes. (On a Mac or Linux? Skip to [macOS and Linux](#getting-started-on-macos-or-linux).)

You don't need a GitHub account, and you won't need to touch any code.

### Step 1: Install the tools

Install these three, in this order.

1. **Node.js.** Go to [nodejs.org](https://nodejs.org/) and click the button for the **LTS** version. Open the file you downloaded and click through the installer. The default choices are fine.
2. **uv.** Click **Start**, type `PowerShell`, and open **Windows PowerShell**. Copy this line, paste it into the PowerShell window (**Ctrl+V**), and press **Enter**:

   ```powershell
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```

   When it's done, close the window. (New tools only show up in windows opened afterward.)
3. **Claude Code.** (_skip if you're using Anthropic's API_) Install it by following the Windows steps at [claude.com/claude-code](https://claude.com/claude-code). Then open a *new* PowerShell window, type `claude`, press **Enter**, and log in with your Claude account. Close the window when you're done.

While you're at it, get your **TypeSafe API key** from [typesafe.ai](https://typesafe.ai), and make sure you're **logged in to X** in your browser. Setup asks for both.

### Step 2: Download Scatha

1. Near the top of this page, click the green **Code** button, then click **Download ZIP**. Your browser saves a file called `scatha-master.zip` in your **Downloads** folder.
2. Open your **Downloads** folder in File Explorer. Right-click `scatha-master.zip` and choose **Extract All...**
3. In the window that opens, click **Browse...**, click **Documents**, and click **Select Folder**. Then click **Extract**.

That gives you a `scatha-master` folder in Documents: the **Scatha folder**, from here on.

> [!IMPORTANT]
> **Leave the Scatha folder where it is.** It *is* the program, and the schedule runs from it. If you move, rename, or delete the Scatha folder later, the schedule stops working. (If you do move it, open PowerShell in its new spot, as in Step 3, and run `powershell -ExecutionPolicy Bypass -File setup.ps1 schedule`.)
>
> This only goes for the Scatha folder. The ZIP file, and the setup file you'll make in Step 4, can stay in Downloads or go in the trash.

Comfortable with git? `git clone https://github.com/mariommg/scatha.git` into a folder you'll keep does the same thing, and `git pull` gets you updates later.

### Step 3: Open PowerShell in the Scatha folder

In File Explorer, open the Scatha folder, right-click an empty spot (not on a file), and choose **Open in Terminal**. On Windows 10, hold **Shift** while you right-click, and choose **Open PowerShell window here**.

Keep this window open for Step 5.

### Step 4: Plan your topics (optional, but it helps)

1. In the Scatha folder, open the **docs** folder and double-click **onboarding.html**. It opens in your browser.
2. Work through the page: your topics, where their notes go, and when Scatha runs.
3. At the end, click **Download my-setup.json**. Your browser saves it in your **Downloads** folder. Leave it there; setup reads it once, and you can delete it afterward.

Rather answer the questions in setup itself? Skip this step.

### Step 5: Run setup

In the PowerShell window from Step 3, paste **one** of these lines and press **Enter**.

If you made `my-setup.json` in Step 4:

```powershell
powershell -ExecutionPolicy Bypass -File setup.ps1 --answers $HOME\Downloads\my-setup.json
```

If you skipped Step 4:

```powershell
powershell -ExecutionPolicy Bypass -File setup.ps1
```

(Does your browser save downloads somewhere other than **Downloads**? Change that part of the first line to match.)

Setup asks one question at a time. Press **Enter** to accept the answer shown in [brackets]. Along the way, it:

- checks the tools from Step 1,
- shows you how to copy your X login from your browser, and asks for it and your TypeSafe key (what you paste stays hidden), then checks that each one works,
- finds your Obsidian vaults and asks where each topic's notes go,
- sets up the schedule,
- and offers a test run on your five newest bookmarks. Those notes go to a preview folder, so you can look them over before anything lands in your vault.

At the end, setup offers to install the `scatha` command. Say yes. Then close PowerShell, open a new PowerShell window (from anywhere), and type `scatha`. It shows you what happened.

Stopped partway? Run the same line again. Setup remembers your answers and picks up where you left off.

## Getting started on macOS or Linux

1. Install [Node.js](https://nodejs.org/) 22 or newer, [uv](https://docs.astral.sh/uv/getting-started/installation/), and [Claude Code](https://claude.com/claude-code) (_skip if you're using Anthropic's API_). Run `claude` once and log in.
2. Download Scatha (the green **Code** button, then **Download ZIP**, or `git clone https://github.com/mariommg/scatha.git`). Unzip it somewhere permanent: the folder *is* the program, and the schedule runs it from there.
3. Open a terminal in the Scatha folder. (macOS: in Finder, right-click the folder and choose **Services → New Terminal at Folder**.)
4. Optional: plan your topics in `docs/onboarding.html`, which saves `my-setup.json` to your Downloads folder.
5. Run `./setup.sh --answers ~/Downloads/my-setup.json`, or `./setup.sh` if you skipped the planner, and follow the prompts.

## Day to day

| Type this | To do this |
|---|---|
| `scatha` | See what happened, and what you can do about it |
| `scatha review` | Open the sorting desk: sort Unsorted, check the close calls |
| `scatha discover` | Look for a topic your Unsorted bookmarks share |
| `scatha run` | Run an import now instead of waiting for the schedule |
| `scatha setup` | Change topics, folders, the schedule, or a login |
| `scatha uninstall` | See everything Scatha put on your computer, and remove what you pick |

## What it costs

Measured on my own backlog, so treat it as one data point:

- **Jev:** about 1.5 cents for roughly 400 sortings, counting every test run while I built and tuned this.
- **Claude Code:** about a quarter of a Claude Pro 5-hour usage window per 100 notes.
- **Anthropic API instead:** roughly a cent per note.

You can set the batch size to match your usage. A big backlog clears little by little, one batch per run.

## Where your stuff goes

Your settings, your topics, and the ledger live in one data folder (`~/.scatha`). Your secrets stay with your account: Windows Credential Manager (handed only to programs that ask for them), or a file only your account can open on macOS and Linux. They never go in a config file, the answers file, or a log.

What leaves your computer: your X session talks to X (it's your login, used the way your browser uses it). Bookmark text goes to TypeSafe for sorting and to Anthropic for writing notes. Scatha fetches the web pages referenced in your bookmarks. If you set up ntfy for notifications, you'll see some text and counts there as well.

Leaving? `scatha uninstall` lists everything Scatha put on your computer and removes only what you pick. Your notes stay; they're yours.

## Honest caveats

> [!WARNING]
> **This uses your X session cookies for automated reading, which X's terms don't love.** It only reads your own bookmarks, one batch per scheduled run, at a modest pace. But it's your account and your call.

- **It reads your newest 1,000 bookmarks.** If you have more than that, the older ones won't come through (yet).
- **X can change under us.** Smaug reads X with a copy of the client code from [bird](https://www.npmjs.com/package/@steipete/bird), kept in `smaug/vendor/bird/`. bird's author has stopped maintaining it, so when X changes its internals, the fix lands here, not upstream. If you also install bird 0.8.0, Scatha tries it as a backup whenever the built-in client fails.
- **Windows is the tested path.** Linux has been through the whole setup, test drive included, up to installing the systemd schedule; a scheduled run hasn't fired there yet. On a Linux server, run `loginctl enable-linger $USER` once, or the schedule stops when you log out; [the runbook](docs/RUNBOOK.md#on-a-server-with-no-screen-linux-over-ssh) covers the rest. macOS is written to spec and has never run, because I don't have a Mac. If you're the first, tell me how it went.
- **Sorting is good, not perfect, and topics blur.** On the first 15 bookmarks I reviewed, Jev matched me 14 times. On 60 close calls I picked out *because* they were hard, it matched me 36 times, and I wasn't always consistent either. So every note lists every topic it fits, not just the folder it's in. Your topics, your wording, your mileage.

## What Scatha isn't good at

Some things it can't see, so it can't sort them well. Knowing them saves you from blaming your topics (which I did, oops):

- **Video.** Only photos are read. A post whose point is a video is judged on its caption, which is why most memes land in Unsorted.
- **Replies under a post.** A thread by the author comes along. Replies the author adds under someone else's post, and other people's comments, don't.
- **Paywalls.** A paywalled article gives Scatha its title and summary, nothing more.
- **Deleted accounts.** A bookmark from an account that's gone can't be read at all.

## When something breaks

`scatha setup status` checks every step, including the logins, and points at the newest log. Running `scatha setup` again fixes whatever it finds broken.

If a scheduled run can't reach X, Jev, or Claude, it stops before fetching. Its log (and phone ping, if you set one up) says what to fix. [docs/RUNBOOK.md](docs/RUNBOOK.md) covers the rest: expired X logins, changing topics, running it right now, and costs.

## Credits

Built on [Smaug](https://github.com/alexknowshtml/smaug) by Alex Hillman (MIT; vendored here with changes, listed in `smaug/CHANGES-scatha.md`), [bird](https://www.npmjs.com/package/@steipete/bird) by Peter Steinberger (MIT; its X client is vendored in `smaug/vendor/bird/`), [Jev](https://typesafe.ai) by TypeSafe, Mozilla's [Readability](https://github.com/mozilla/readability), and Claude.

## License

MIT, see [LICENSE](LICENSE). The vendored Smaug and bird code keep their own MIT licenses (`smaug/LICENSE`, `smaug/vendor/bird/LICENSE`).
