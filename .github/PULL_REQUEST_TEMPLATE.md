<!--
  This text appears in the description box the moment someone opens a pull request —
  which is the only point where telling them is still cheap. CONTRIBUTING.md says the
  same thing, but a contributor who has already written the patch reads it too late.
-->

## Pull requests are not accepted on this repository

Not a judgement about the change — a property of how the repo is built.

Every file here is **generated output**. The source of truth is a working harness plus a
push register; each release regenerates the shipped files and overwrites the tree. A
merged edit would be silently reverted by the next release.

**Please open an [issue](../../issues) instead.** It reaches the source: a described
problem can change the harness these files come from, and the change arrives here in a
later release. That is how every fix in this repo has happened.

If you want the change for yourself, the [MIT licence](../LICENSE) already covers it —
fork it and make it yours. Given that these files are generated, your fork is a better
home for your change than this tree.

See [CONTRIBUTING.md](../CONTRIBUTING.md).
