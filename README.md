# gtd

A terminal task app built on David Allen's *Getting Things Done*. It serves both vaults: `~/mindpalace/gtd.md` (work) and `~/homepalace/gtd.md` (home). The design records live in `~/mindpalace/system/docs/gtd-app-design.md` and `gtd-migration-design.md`.

- **Open the app with `gtd`.** Press `?` for every key and `/` to search tasks and commands.
  - `c` clarifies In, or re-clarifies any item on one card; `m` project, `l` context, `d` dates and `p` waiting-for change one field.
  - `v` cycles the vault view (work, home, both), `Enter` on a project opens its page, and `L` edits the contexts.
- **Automation uses the same command.**
  - `gtd add`, `gtd propose`, `gtd done`, `gtd find`, `gtd list`, `gtd expire` and `gtd sweep`.
  - `gtd --help` describes each one, and `gtd --file <path>` works on a single file.
  - Scripts and skills never edit `gtd.md` by hand, so every write goes through one parser and one atomic write.
- **Areas are listed in `~/.config/gtd/config.toml`.** The format is documented at the top of `gtd/config.py`, and the contexts editor (`L`) rewrites only the `contexts = [...]` line.
- **Run the tests with `.venv/bin/pytest gtd -q`.**
- **The launcher builds its own venv.** `bin/gtd` creates `.venv` with Textual on first run, and `~/.local/bin/gtd` links to it.
- **Write links in Obsidian's own forms, inside the task text.** Use `[[note|label]]` for a vault note and `[label](url)` for anything else, so the same line works in Obsidian and in the app. In the app, `o` opens a link. An automated proposal's origin goes in `--src`, which is a link as well.
