# Python Decoder (@pythondecoder101): relaunch and first 3 videos

*2026-11-24. Status: proposed; nothing scripted, rendered or published.*

## Where the channel stands

https://www.youtube.com/@pythondecoder101 has 60 subscribers and 11 long videos plus 4 Shorts, all from 11 Jan 2023 to
30 Jul 2023 (read with yt-dlp, 2026-11-24). Every video is a generic beginner topic, 49 to 322 views:

| Video | Views |
|---|---|
| File handling: read, write, append, delete | 322 |
| PyCharm installation step by step | 272 |
| Loops and conditionals | 162 |
| Part 1: introduction to Python | 125 |
| Lists, data structures, functions, lambdas, try/except | 49 to 109 |

Shorts: "Bite-sized Python challenge" Ep 1 to 4 (even numbers, max/min, reverse, sum/average).

The two best performers are a **practical task** (files) and **setup** (PyCharm install). Pure concept videos did
worst. Generic beginner topics compete with full courses that have tens of millions of views.

## Positioning

**"Modern Python, decoded."** Short, practical videos on what changed in Python and what people search for now,
each ending in something working on screen. English only (the talking-avatar lip sync is English only).

- **Format:** an 8 to 12 min screen recording with the user's cartoon avatar bottom-right (`ve talking-avatar`,
  hybrid lip sync, speech bubbles on key or fun points), plus 2 to 3 Shorts cut from each video.
- **Voice:** ElevenLabs "Harish – Professional" from the script (`.env` key).
- **Cadence:** 1 long video a week, Shorts in between. Revive "Bite-sized Python challenge" as the Shorts series.
- **Old videos:** keep them, grouped in a "Python basics (2023)" playlist. Don't delete them.

## Demand evidence (2026-11-24)

YouTube autocomplete (Google suggest, ds=yt) and the top search results with view counts (yt-dlp `ytsearch8`):

- **uv:** suggestions "python uv tutorial / setup / install / vs pip / vscode / fastapi". Top videos: Corey Schafer
  307k, Tech With Tim 196k, Dave Ebbelaar 71k, Matt Palmer 57k, Krish Naik 54k. Proven, evergreen demand, and a
  natural follow-up to the channel's best-performing setup video.
- **Python 3.15:** the stable release shipped in October 2026 (Simon Willison, 12 Oct 2026). Most existing videos
  cover pre-release betas: ArjanCodes 170k, Indently 80k / 31k / 22k, anthonywritescode 19k. Features: lazy
  imports, `frozendict`, `sentinel()`, UTF-8 by default, unpacking in comprehensions, the Tachyon sampling profiler,
  and a stable ABI for free-threaded builds. The timely topic is "it's out, here's what to use".
- **MCP servers in Python:** suggestions "python mcp server example / vscode / fastmcp / sdk". Videos sit at 1k to 41k
  (ZazenCodes 41k, Microsoft Reactor 17k). Demand is rising and competition is still beatable for a small channel.
- **AI agents from scratch:** huge demand (Tech With Tim 1.0M, Dave Ebbelaar 479k, freeCodeCamp 300k to 690k) but
  saturated by big channels. Better as a later video once the channel has some traction.
- **Automation projects:** 2M-view evergreen category (Internet Made Coder, freeCodeCamp); a good series later.

## The next 3 videos

### 1. Python 3.15 is out: 6 features you'll actually use

- **Why now:** the release is about 6 weeks old; most competing videos are beta previews.
- **Show:** install 3.15 with uv in one line (sets up video 2). Then one before/after demo each:
  - lazy imports (startup time measured live)
  - `frozendict`
  - `sentinel()`
  - unpacking in comprehensions
  - UTF-8 by default (the Windows `open()` bug that goes away)
  - the Tachyon profiler finding a slow function
- **Length:** about 9 min. **Shorts:** "frozendict in 30 s", "the Python bug 3.15 just fixed (UTF-8)", "profile any script in one command".
- **Title options:**
  - Python 3.15 Is Out: 6 Features You'll Actually Use
  - Python 3.15: What Changed and What to Use Today
  - Upgrade to Python 3.15? These 6 Features Say Yes
- **Thumbnail:** avatar pointing at a big "3.15" plus a `frozendict` code chip; 3-word hook "6 NEW TOOLS".
- **Check before scripting:** run every demo on the real 3.15 build and name only features that work in the stable release.

### 2. Stop using pip: uv in 10 minutes (2026 setup)

- **Why:** the strongest evergreen demand (5 videos at 50k to 300k), and it follows the channel's best "setup" video.
- **Show:** install uv (Windows, macOS, Linux), `uv init`, `uv add`, `uv run`, run a script with inline dependencies,
  `uv python install`, `uv tool install ruff`, and a pip+venv vs uv timing race on the same project. End with "your
  2026 Python setup in VS Code".
- **Length:** about 10 min. **Shorts:** "pip vs uv race", "run a script without a venv", "install any Python version in one command".
- **Title options:**
  - Stop Using pip: uv in 10 Minutes (2026 Setup)
  - uv Replaces pip, venv and pyenv: Full Beginner Guide
  - The Fastest Python Setup in 2026 (uv Tutorial)
- **Thumbnail:** a race visual, pip snail vs uv rocket, with timings from the real race; hook "100× FASTER?" only if the measured race supports it.

### 3. Build your first MCP server in Python (FastMCP): let AI use your code

- **Why:** rising demand ("python mcp server …" suggestions), moderate competition, and it rides the AI-agent wave
  without fighting the million-view agent courses head on.
- **Show:** a 20-line FastMCP server exposing 2 real tools (for example "search my notes folder" and "convert units").
  Connect it to Claude Desktop and to VS Code, ask a question, and watch the tool get called. Then cover the
  error-handling and security basics: what to never expose. Uses uv from video 2.
- **Length:** about 12 min. **Shorts:** "what MCP is in 45 s", "a 20-line AI tool", "never expose this in an MCP server".
- **Title options:**
  - Build Your First MCP Server in Python (FastMCP Tutorial)
  - Give AI Your Own Python Tools: MCP in 12 Minutes
  - MCP Explained by Building One (Python + FastMCP)
- **Check before scripting:** use the current FastMCP and MCP SDK versions (look them up when scripting), and test
  the Claude Desktop config on this machine.

Order: 1 first while 3.15 is fresh, then 2 (video 1 points to it), then 3 (builds on 2). The three make a
"Modern Python 2026" playlist.

## Production per video (existing tools, no new spend expected)

1. Script in `videos/<date>/<slug>/script.md`, with demos tested on the real versions. Every number on screen comes from a real run.
2. Voice: ElevenLabs from the script (payg character budget; quote the character count before generating).
3. Screen: a real terminal and VS Code recording, or rendered panes as in the talking-avatar pilot.
4. `ve talking-avatar --script ... --bubbles bubbles.yaml` (hybrid lip sync, about 1 min CPU per minute of audio).
5. Captions, chapters and a thumbnail via `ve`. 3 thumbnail concepts, 320 px legibility check.
6. Publish through social-engine only after the user approves the final cut, title and thumbnail.

## Open decisions for the user

- Rename or rebrand the channel (banner, "Coder Corner" intro) or keep "Python Decoder".
- Upload day and time (no analytics yet for the relaunch; start with a weekend slot and read Studio after 48 h).
- Whether a Telugu track comes later. Autocomplete shows "python for beginners in telugu", but the avatar lip sync is English only today.

Sources: [Python 3.15 what's new](https://docs.python.org/3/whatsnew/3.15.html),
[Simon Willison on 3.15](https://simonwillison.net/2026/Oct/12/python-3-15/),
[Real Python popular tutorials](https://realpython.com/popular-python-tutorials-2025/), YouTube autocomplete and
search results read on 2026-11-24.
