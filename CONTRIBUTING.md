# Contributing

**This repository has one author, and pull requests are not accepted.** That is a
property of how it is built, not a judgement about anyone's patch.

Every file here is **generated output**. The source of truth is a working harness plus a
push register; each release regenerates the shipped files from it and overwrites whatever
is in the tree. A merged edit to any file in this repo would be silently reverted by the
next release, with nobody noticing — so a PR cannot land in any durable sense, and
leaving one open would waste your time rather than respect it.

## What does work

**Open an [issue](../../issues).** It is the channel with a path to the source: a
described problem can change the harness these files are generated from, and the change
then arrives here on its own in a later release. That is how every fix in this repo has
happened.

Useful things to put in an issue:

- **a bug** — what you ran, what happened, what you expected. If a gate fired when it
  should not have, or did not fire when it should, say which one.
- **"how would I adapt this to my setup?"** — several skills read settings specific to
  one environment. Naming the one that fought you is the most useful report there is.
- **a gap** — something the framework documents but does not do. Those get fixed fast,
  because a claim the code does not keep is the defect this project takes most
  seriously.

## What you can do without asking

The [MIT licence](LICENSE) is the real answer to "can I use this": fork it, change it,
ship it, sell it. Keep the copyright notice, and read [NOTICES.md](NOTICES.md) for the
third-party material the MIT grant cannot cover.

A fork is not second best here. It is the supported way to make this yours — and given
that the files are generated, your fork is a better home for your changes than this tree
could ever be.
