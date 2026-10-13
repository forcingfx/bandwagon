# bandwagon

A terminal app for David Allen's *Getting Things Done*, over one plain markdown file per
area of your life. The command is `gtd`: with no arguments it opens the app, with a
command it is a scripting CLI that reads and writes the same file. The file stays plain
`- [ ]` lines under six `##` headings, so it is readable and editable in any editor or
notes app, and it works unchanged inside an Obsidian vault.

![Next Actions in the app](docs/screenshot.svg)

## Install

macOS or Linux, with [uv](https://docs.astral.sh/uv/) (Python 3.11 or newer is fetched if
you have none):

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh        # if you don't have uv
uv tool install git+https://github.com/forcingfx/bandwagon
gtd --version
```

Upgrade with `uv tool upgrade bandwagon`, or pin a release with
`uv tool install git+https://github.com/forcingfx/bandwagon@v0.1.0`.

## Set up a task file

Run `gtd`. With nothing configured it opens on the Files screen: press `n` to create a
task file (the default is `gtd.md` in the current folder, so run it inside your notes
folder to keep it there), or `a` to add a file you already have. Press `F` in the app at
any time to come back: add, move, rename or remove areas.

Every file is looked at before it loads. A gtd file or a missing one goes straight in. A
plain markdown file is offered for adoption: the six lists are appended and its own
lines stay. A file with stray lines inside the lists, or extra lists, says so and keeps
them. A file written by a newer gtd, or one that can't be read, is refused.

From a script, `gtd init PATH [--area NAME]` does the same without prompts.

The config lives in `~/.config/gtd/config.toml` (or `$GTD_CONFIG`). The Files screen
writes it for you; this is the full format:

```toml
# gtd config. One [areas.NAME] per task file; the app shows them side by side.
capture = "work"                 # where captures land when every area is showing
contexts = ["@calls", "@computer", "@office", "@home", "@errands", "@agendas"]   # offered in clarify, in order

[areas.work]
file = "~/notes/work/gtd.md"
state_dir = "~/notes/work/.gtd"            # proposal counts, overflow, expired log (gtd propose, gtd expire)
proposal_cap = 5                           # automated proposals per day; omit for no cap
proposal_reserve = { process = 3 }         # slots of the cap kept for one caller (propose --caller)

[areas.home]
file = "~/notes/home/gtd.md"
completions_log = "~/notes/home/completions.md"   # a table of recurring and one-off ticks, appended to
```

`gtd --file PATH` works on one file with no config at all.

### The file

```markdown
<!-- gtd v1 · last-review: 2026-10-13 -->

## In
- [ ] Call Anna re Q4 budget @calls +budget due:2026-10-17 added:2026-10-13
## Next Actions
## Projects
## Waiting For
## Someday/Maybe
## Done
```

Tags sit at the end of a line: `@context` (several allowed), `+project`, `waiting:who`,
`every:weekly`, `start:`, `due:`, `added:`, `done:`, `star:` and `src:` (where an automated
proposal came from). Anything the parser can't read is kept exactly as written. Done
items are pruned after 14 days unless the header says `prune: manual`.

Links go in the task text in markdown's own forms: `[label](url)` for anything, and
`[[note|label]]` for a note next to the file (an Obsidian wikilink). `o` opens a link
from the app, with `open` on macOS and `xdg-open` on Linux; if that fails the link is
copied instead (`pbcopy`, or `wl-copy`, `xclip` or `xsel` on Linux). Under Sway, an
Obsidian note is also pulled to the current workspace.

## Use the app

Press `?` for every key and `/` to search tasks and commands.

- `a` captures (Tab saves and captures another). `c` clarifies In, or re-clarifies any
  item on one card: Allen's questions, one screen.
- `m` project, `l` context, `h` dates and `p` waiting-for change one field. `e` edits the
  text. `Space` ticks. `z` undoes.
- `s` stars an item; starred items gather on the Focus tab after In. `d` shows deferred
  tasks whose start date is still ahead.
- `v` cycles the areas (one, the other, all). `Enter` on a project opens its page. `L`
  edits the contexts, `F` the files.
- `w` runs the weekly review as a guided tour of the lists.
- Next Actions lists one-offs by project under a header bar (tasks without a project
  first), then the recurring tasks in their own block. A project with more than five
  shows five and a "show N more" row.
- The Project and Context rows are search boxes: type to narrow, `↑ ↓` move, `Enter`
  takes, and the last row creates a new one from the words typed.

Edits made outside the app (an editor, a sync client, the CLI) appear on their own.

## Use the command line

`gtd --help` lists every command with examples and `gtd help COMMAND` shows one.

```sh
gtd add "Call Anna re Q4 budget" --context calls --due fri
gtd done "Q4 budget"
gtd find passport                 # a picker in a terminal; a listing from a script
gtd list --area home --section next   # JSON
```

For automation: `gtd propose "text" --src LINK` suggests a task with where it came from,
capped per day by `proposal_cap` (extras are parked in `task-overflow.md` under the
area's `state_dir`), with `proposal_reserve` keeping slots for a named caller
(`--caller NAME` or `$GTD_CALLER`). `gtd expire` drops proposals left unsorted for 7
days and `gtd sweep` files checkboxes ticked in the file itself; both are meant for a
timer. Without a terminal every command prints or refuses, and never prompts. Scripts
should never edit the file by hand: every write goes through one parser, one lock and
one atomic rename.

## Development

```sh
git clone https://github.com/forcingfx/bandwagon && cd bandwagon
uv sync
uv run gtd
uv run ruff check .
uv run pytest            # about two minutes: most tests drive the app headlessly
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the commit convention and how a release is cut.

## License

[MIT](LICENSE).
