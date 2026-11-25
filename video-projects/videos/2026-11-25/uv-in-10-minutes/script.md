# Stop Using pip: uv in 10 Minutes (2026 Setup)

Python Decoder (@pythondecoder101), video 2. English narration, ElevenLabs "Harish – Professional".
Every number below comes from a real run on this machine (uv 0.13.9, Python 3.15.2, `demos/`, 2026-11-25; log in
`README.md`). `[S#]` marks a scene change. The narration text is `audio/narration.txt`.

## [S0] Hook: the race (split terminal)

This is pip installing six popular packages: FastAPI, pandas, requests, rich, httpx and pydantic. About nineteen
seconds. Now the exact same packages with uv. About five seconds. And once uv has them cached, a fresh install takes
five hundredths of a second. uv replaces pip, venv, pyenv and pipx with one tool. In the next few minutes, I will set
it up from scratch, the 2026 way. And if you learned uv from an older tutorial, stay with me, because a few things
changed, and I will show you what.

## [S1] Install uv (docs.astral.sh installation page, terminal)

uv is a single program, written in Rust by Astral, the team behind Ruff. On macOS and Linux, paste this one line into
your terminal. On Windows, use this PowerShell line instead. Homebrew, winget, and even pip install uv work too. Then
check it with uv dash dash version. I am on 0.13.9, the latest release right now.

## [S2] Python versions (terminal)

First surprise: you do not need to install Python separately anymore. uv python install downloads a ready-made Python.
Here it fetched Python 3.13 in about three seconds. No installer, no admin rights. With no version number, you get the
newest stable one, 3.15. uv python list shows every Python on your machine, including the ones that were already there.
And uv run dash dash python 3.14 runs anything on that exact version.

## [S3] uv init (terminal, editor tree)

Now a real project. uv init weather. Here is what changed. Older tutorials show a single main dot py file. Since uv
0.12, uv init creates a proper package. Your code lives in src slash weather, and pyproject dot toml gets a command
entry and a build system. That avoids the classic import headaches later. If you want the old flat layout, add dash
dash no package. And notice: there is no virtual environment yet.

## [S4] uv add, lock file, tree (terminal, editor)

Let's add two libraries. uv add requests rich. In about one second, uv created the virtual environment, picked Python
3.15, resolved ten packages and installed them. pyproject now lists both dependencies, and a new file appeared: uv dot
lock. The lock file pins the exact version of every package, so your teammate, your server, and you, a year from now,
all get the same environment. Commit it to git. uv tree shows who depends on what.

## [S5] uv run, uv sync (editor, terminal)

Here is a tiny app that fetches the current temperature for three cities and prints a table. To run it, you do not
activate anything. Just uv run weather. uv checks the lock file, syncs the environment if needed, and runs your code.
Cloned a project from GitHub? Run uv sync. Ten packages installed, the exact versions from the
lock file.

## [S6] Scripts with inline dependencies (editor, terminal, PEP 723 page)

This is my favourite feature: single file scripts. This script needs rich. Normally, that means making a virtual
environment just for one file. Instead, uv add dash dash script writes the dependency into the script itself, in this
comment block. It is a Python standard, PEP 723. Now uv run hello rich dot py. uv builds a cached, throwaway
environment and runs it. Send this one file to a friend who has uv, and it just works.

## [S7] Tools: uv tool install, uvx (terminal)

Command line tools like Ruff do not belong inside your project. uv tool install ruff installs it once, in its own
isolated environment, and it works everywhere. Want to try a tool just once? uvx runs it without installing anything.
uvx ruff check, and it finds the unused import in my file, and even offers to fix it.

## [S8] Moving an old project (terminal)

Already have a project with a requirements dot txt file? uv add dash r requirements dot txt moves every package into
pyproject in one go. Or keep your old workflow and just put uv in front: uv pip install dash r requirements dot txt.
That is exactly the command I used in the race.

## [S9] VS Code and recap (editor, checklist card)

In VS Code, uv's dot venv folder sits right inside the project, so VS Code finds it. Open the command palette, choose
Python: Select Interpreter, and pick dot venv. Quick recap. uv python install for Python. uv init and uv add for
projects. uv run to run. uv add dash dash script for single files. And uvx for tools. One tool instead of five. In the
next video, we use uv to build an MCP server, so AI can call your own Python code. Subscribe, so you do not miss it.
