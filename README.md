# gtd

A terminal task app built on David Allen's *Getting Things Done*. It serves both vaults: `~/mindpalace/gtd.md` (work) and `~/homepalace/gtd.md` (home). The design records live in `~/mindpalace/system/docs/gtd-app-design.md` and `gtd-migration-design.md`.

- **Open the app with `gtd`.** Press `?` for every key and `/` to search tasks and commands.
  - `c` clarifies In, or re-clarifies any item on one card; `m` project, `l` context, `h` dates and `p` waiting-for change one field.
  - `s` stars an item, and starred items gather on the Focus tab after In; `d` shows deferred tasks.
  - `v` cycles the vault view (work, home, both), `Enter` on a project opens its page, and `L` edits the contexts.
  - Next Actions lists one-offs by project under a header bar (tasks without a project first), then the recurring tasks in their own block. A project with more than five shows five and a "show N more" row; `Enter` or a click expands it, and "show less" folds it.
  - Rows read project, action, chips, dates, then contexts; the project label links to its note and is left off where a header already names it.
  - The Project and Context rows (`m`, `l`, and the clarify card) are search boxes: type to narrow, `↑ ↓` move, `Enter` takes, and the last row creates a new one from the words typed. Digits pick by number while the box is empty; on Context, `Space` ticks several and "Edit the list…" opens the editor.
- **Automation uses the same command.**
  - `gtd add`, `gtd propose`, `gtd done`, `gtd find`, `gtd list`, `gtd expire` and `gtd sweep`.
  - `gtd --help` (or `gtd help`) lists them by goal with examples, `gtd help <command>` shows one, and `gtd --file <path>` works on a single file. The text lives in `gtd/helptext.py`.
  - `gtd find` matches every word in any order, in the text or the tags (`+project`, `@context`, a person, `waiting:name`). In a terminal it opens an inline picker over the open matches: `Space` ticks, `s` stars, `/` or `Tab` edits the words, `Esc` quits. `gtd done` opens the same picker when its words match several open items, and refuses when they match none.
  - Without a terminal (scripts, skills) both print or refuse as before and never prompt.
  - `gtd propose` stops at the area's `proposal_cap` a day and parks the rest in `task-overflow.md`. `proposal_reserve = { process = 3 }` keeps three of those slots for the caller named `process`: a caller says who it is with `--caller NAME` or `$GTD_CALLER`, and any other caller, or none, gets only the slots nobody else has reserved. The day's counts per caller are in `gtd-proposals.json` in the area's `state_dir`.
  - Scripts and skills never edit `gtd.md` by hand, so every write goes through one parser and one atomic write.
- **Areas are listed in `~/.config/gtd/config.toml`.** The format is documented at the top of `gtd/config.py`, and the contexts editor (`L`) rewrites only the `contexts = [...]` line.
- **Run the tests with `.venv/bin/pytest gtd -q`.**
- **The launcher builds its own venv.** `bin/gtd` creates `.venv` with Textual on first run, and `~/.local/bin/gtd` links to it.
- **Write links in Obsidian's own forms, inside the task text.** Use `[[note|label]]` for a vault note and `[label](url)` for anything else, so the same line works in Obsidian and in the app. In the app, `o` opens a link. An automated proposal's origin goes in `--src`, which is a link as well.
