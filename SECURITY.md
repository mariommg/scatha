# Security

Scatha holds keys to your accounts: your X login cookies, your TypeSafe key, and (if you use it) your Anthropic key. It also reads links from posts written by strangers. If you find a way to misuse either of those, I want to hear about it before anyone else does.

## How to report

Use **Report a Vulnerability** on the [Security tab](https://github.com/mariommg/scatha/security/advisories/new). Only you and I will see the report.

Please don't open a public issue for a security problem.

Tell me what you found, the steps to make it happen, and which version or commit you tested. You don't need a fix.

## What counts

- Scatha exposes a secret: in a log, a note, the status note, an error message, or a file other users can read.
- A bookmark can make Scatha fetch an address on your own network or machine (the link guard should stop this).
- Scatha writes over or deletes a file it didn't create.
- Content from a bookmark can run code or commands on your machine.

## What doesn't

- Bugs in X, Obsidian, Claude Code, or TypeSafe themselves. Report those to them.
- Upstream Smaug. If the bug is also in [alexknowshtml/smaug](https://github.com/alexknowshtml/smaug), tell me, and I'll pass it on.
- Someone who already controls your user account reading your secrets. Scatha can't defend against that.

## What to expect

Scatha is a one-person project. I'll reply within a week or so, and I'll tell you when a fix is out. Only the newest release gets fixes.
