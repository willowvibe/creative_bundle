# Build Your First MCP Server in Python (FastMCP Tutorial)

Python Decoder (@pythondecoder101), video 3. English narration, ElevenLabs "Harish – Professional".
Every output below is from a real run on this machine (FastMCP 4.2.6, MCP SDK 2.3.0, uv 0.13.9, Python 3.15.2,
Claude Code 2.1.325; `demos/`, 2026-11-25; log in `README.md`). `[S#]` marks a scene change.

## [S0] Hook: an AI using my code (terminal, Claude Code run)

I asked Claude: how far is my Goa trip, in miles? It searched my own notes, found six hundred and fifty kilometres, and
called a converter to get about four hundred and four miles. Both of those tools are tiny Python functions I wrote.
The thing that connects them to the AI is called MCP. In the next few minutes, we build this server from scratch, test
it, connect it, and make sure it cannot leak your secrets.

## [S1] What MCP is (animated diagram: AI app → MCP → your server → tools)

MCP, the Model Context Protocol, is an open standard for connecting AI apps to tools and data. Think of it like a USB-C
port for AI. You write a server once, and Claude, VS Code, Cursor and other apps can all use it. Your server offers
tools, and the AI decides when to call them.

## [S2] Setup with uv (terminal)

We will use FastMCP, the most popular Python framework for MCP. It is on version four now. Following my uv video, this
is a single file script. uv add dash dash script server dot py fastmcp. That writes the dependency into the file, so
there is no virtual environment to manage.

## [S3] Tool one: search my notes (editor, then diagram: function → what the AI sees)

Here is the whole server. We create a FastMCP app called My Notes. Then each tool is just a Python function with the at
mcp dot tool decorator. This one searches my notes folder for a word and returns the matching lines. The important
part: the type hints and the docstring are not decoration here. FastMCP turns them into the description the AI reads,
so a clear docstring is how the AI knows when to use your tool.

## [S4] Tool two: convert units (editor)

The second tool converts kilometres to miles, kilograms to pounds, and Celsius to Fahrenheit. And at the bottom, mcp
dot run starts the server. That is it. About thirty five lines, two tools.

## [S5] Test it without any AI (terminal)

Before connecting an AI, test it yourself. fastmcp list shows both tools, with the inputs the AI will see. fastmcp call
runs one directly. Search for goa, and it finds my trip plan. Convert six hundred and fifty kilometres, and you get four
hundred and three point nine miles. If this works, the AI side will work too.

## [S6] Connect it to an AI app (terminal, then diagram: the three real tool calls)

Now let's connect it. fastmcp install has ready commands for Claude Desktop, Claude Code, Cursor and more, or mcp json
prints the config to paste into any app. I gave this config to Claude Code and asked my question. Watch the tool calls.
It searched for Goa, then searched again for kilometres, then called convert units. Three calls, all decided by the
AI, all running my Python on my machine.

## [S7] Never expose this (editor, terminal, diagram: hidden instruction → leak → path check blocks it)

Now the part most tutorials skip. Here is a read note tool that takes a file name. Looks harmless. But ask it for dot
dot slash dot env, and it walks out of the notes folder and returns my API key. An AI can be tricked into asking for
exactly that, for example by a hidden instruction in a web page it reads. The fix is two lines: resolve the path, and refuse anything outside the notes folder, or anything that
is not a markdown file. Now the same request gets an error. My rules: never expose a tool that runs shell commands or
takes any path, keep tools read only unless they must write, and never put secrets where a tool can reach them.

## [U0] Real use case: talk to your database (terminal: shop.db)

Now a real use case. Every small business has a database, and most owners cannot write SQL. So let's give an AI safe
access to one. This is a demo shop database: eight products, twelve customers, and 360 orders.

## [U1] The server: two tools, two locks (editor: db_server.py)

The server is just two tools. list tables returns every table with its columns, so the AI can learn the schema by
itself. run query takes one SQL statement and returns up to fifty rows. Now notice the two locks. First, the tool only
accepts SELECT queries. Second, the database file itself is opened read only. So even if a query slips past the first
check, SQLite refuses to write.

## [U2] How a question flows (animated diagram)

Here is how a question flows. You ask in plain English. The AI calls list tables to learn the schema, writes the SQL
itself, runs it through run query, and turns the rows into an answer.

## [U3] The real run (terminal, Claude Code)

I asked Claude: which product made the most money in October, and who was our best customer that month? It read the
schema first, then wrote five queries on its own, with joins across all three tables. Here is one of them: it joins orders, products and customers, filters to
October, skips cancelled orders, and sorts by money spent. I did not write a single line of that SQL. The answer: the Cold Brew Kit,
19,000 rupees from 20 units. And the best customer was Arjun Patel from Pune, with 18,899 rupees. It even noticed that
Masala Chai sold the most units, but came fifth on revenue.

## [U4] Try to delete (terminal, diagram)

Then I asked it to delete all cancelled orders. Claude stopped and asked me first, and pointed out that this connection
only allows SELECT. So let's test the locks ourselves. A plain DELETE is refused by the first check. A sneaky one that
starts with WITH gets past the first check, but SQLite blocks it: attempt to write a readonly database. All 360 orders
are still there.

## [U5] The pattern (diagram)

That is the pattern for any real MCP server: small, clear tools, a schema the AI can discover, and safety enforced in
your code and in the system underneath, not just in the prompt. Swap SQLite for your company database, and you have an
analyst that answers questions in seconds. The same pattern works for your expenses sheet, your support tickets,
or your server logs: expose them read-only, describe the tools clearly, and let the AI do the digging.

## [S8] Recap (cheat sheet)

Quick recap. uv add dash dash script for the setup. At mcp dot tool on a typed function with a good docstring. fastmcp
list and fastmcp call to test. fastmcp install to connect. And lock down every path. What tool would you give your AI
first? Tell me in the comments, and subscribe for more modern Python.
