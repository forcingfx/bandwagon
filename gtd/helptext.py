"""What `gtd --help`, `gtd help` and `gtd <command> --help` print. Help never acts: it
reads no config, writes nothing and never opens the app. Plain ASCII, 80 columns."""
from __future__ import annotations

import argparse

WIDTH = 80

TOP = """\
gtd: capture, sort and tick off your tasks, work and home, from one place.

Usage: gtd [COMMAND] [WORDS...]     With no command, gtd opens the app.

Everyday
  gtd                    Open the app. Press ? in it to see every key.
  gtd add "text"         Capture a task to In, the inbox you sort later.
  gtd done "words"       Tick off the open task matching your words; a picker
                         opens if several match.
  gtd find "words"       Find tasks by words or tags; in a terminal, a picker
                         to tick (Space) or star (s) them.

Automation (for scripts, skills and timers)
  gtd propose "text" --src LINK
                         Suggest a task to In, with where it came from.
  gtd list               Print every task as JSON.
  gtd expire             Remove suggestions left unsorted in In for 7 days.
  gtd sweep              File tasks you ticked in Obsidian: done, or due again.

Options
  --area NAME            Use one area, work or home. add defaults to work,
                         list, expire and sweep to both.
  --file PATH            Use this one gtd.md file instead of your areas.
  -h, --help             Show this help. gtd help COMMAND: one command's help.

Examples
  gtd add "Call Anna re Q4 budget" --context calls --due fri
      Captured to work In: Call Anna re Q4 budget
  gtd add "Renew passport" --area home
      Captured to home In: Renew passport
  gtd done "Q4 budget"
      Ticked in work: Call Anna re Q4 budget
  gtd find passport
      One line per match: area, list, open or done, task.

Full docs: ~/gtd/README.md, design in ~/mindpalace/system/docs/gtd-app-design.md
"""

# Per command: what it does (description) and real invocations (epilog).
COMMANDS = {
    "add": ("Capture a task to In, the inbox you sort later in the app. Flags such as\n"
            "--context and --due sort it now; --section puts it on another list.",
            'Examples:\n'
            '  gtd add "Book dentist" --area home --due 2026-10-20\n'
            '      Captured to home In: Book dentist\n'
            '  gtd add "Draft offsite agenda" --section next --context computer\n'
            '      Captured to work Next Actions: Draft offsite agenda'),
    "propose": ("Suggest a task to In from a script or skill, with a link to where it\n"
                "came from. Past the area's daily cap, extras are parked in\n"
                "task-overflow.md instead of In.",
                'Example:\n'
                '  gtd propose "Reply to Ben re hiring" --src https://x.slack.com/archives/1\n'
                '      Proposed to work In: Reply to Ben re hiring'),
    "done": ("Tick off the open task matching your words (every word, any order, text\n"
             "or tags). A repeating task rolls to its next due date. If several match,\n"
             "a picker opens in a terminal; without one, or if none match, nothing\n"
             "changes and the matches are listed.",
             'Example:\n'
             '  gtd done "pay rent"\n'
             '      Ticked in home: Pay rent (next due 2026-11-01)'),
    "find": ("Find tasks in every area by your words, in any order, in the text or the\n"
             "tags (+project, @context, a person, waiting:name). In a terminal it opens\n"
             "a picker over the open matches: Space ticks, s stars, / or Tab edits the\n"
             "words, Esc quits. Without a terminal it prints every match, open or done,\n"
             "and exits 1 when nothing matches.",
             'Example:\n'
             '  gtd find plumber\n'
             '      One line per match: area, list, open or done, task.'),
    "list": ("Print every task as JSON, one object per task, for scripts and views.",
             'Example:\n'
             '  gtd list --area home --section next\n'
             '      [ {"area": "home", "section": "Next Actions", "text": ...} ]'),
    "expire": ("Remove suggestions (from gtd propose) left unsorted in In for N days.\n"
               "Each one is logged to gtd-expired.md, kept 28 days. Run by a timer.",
               'Example:\n'
               '  gtd expire --days 7\n'
               '      Expired from work In: Reply to Ben re hiring'),
    "sweep": ("File the tasks you ticked in Obsidian: one-off tasks move to Done,\n"
              "repeating ones roll their due date forward. Run by a timer.",
              'Example:\n'
              '  gtd sweep\n'
              '      Done in home: Call plumber   (or: Nothing ticked.)'),
}

# Arguments argparse would otherwise list bare, by destination name.
ARGS = {
    "text": "the task, in quotes",
    "words": "words from the task's text",
    "area": "work or home (default: work)",
    "area_all": "only this area, work or home (default: both)",
    "section": "the list to use, e.g. in, next, waiting, someday",
    "src": "link to where the suggestion came from",
    "days": "age in days before a suggestion goes (default: 7)",
}


class Formatter(argparse.RawDescriptionHelpFormatter):
    """Keeps our own line breaks and never runs wider than 80 columns."""

    def __init__(self, prog: str) -> None:
        super().__init__(prog, width=WIDTH, max_help_position=26)


class TopHelp(argparse.Action):
    def __init__(self, option_strings, dest=argparse.SUPPRESS, default=argparse.SUPPRESS, help=None):
        super().__init__(option_strings, dest=dest, default=default, nargs=0, help=help)

    def __call__(self, parser, namespace, values, option_string=None):
        print(TOP, end="")
        parser.exit()


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="gtd", add_help=False, color=False, formatter_class=Formatter)
    p.add_argument("-h", "--help", action=TopHelp, help="show this help")
    return p


def finish(sub) -> None:
    """Gives each command its description, example and argument help."""
    for name, cmd in sub.choices.items():
        cmd.description, cmd.epilog = COMMANDS[name]
        cmd.formatter_class, cmd.color = Formatter, False
        for action in cmd._actions:
            if action.help is None and action.dest in ARGS:
                both = action.dest == "area" and name in ("list", "expire", "sweep")
                action.help = ARGS["area_all" if both else action.dest]
            if action.dest == "section":
                action.metavar = "LIST"


def expand(argv: list[str]) -> list[str]:
    """`gtd help` is `gtd --help`; `gtd help add` is `gtd add --help`."""
    if argv[:1] == ["help"]:
        return argv[1:2] + ["--help"]
    return argv
