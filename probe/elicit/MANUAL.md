# Manual client runs: what a real client does with an elicitation

The scripted runs in `raw_client.txt` prove the SDK works both ways. They
prove nothing about what a human sees, because the client there was a Python
callback. These steps put the same toy server in front of Claude Desktop and
Claude Code.

The toy server lives in `scratchpad/elicit/server.py`, which is gitignored, so
these paths are local to this machine.

```
server:  /home/n1trolab/projects/agentlock-browser/scratchpad/elicit/server.py
python:  /home/n1trolab/projects/agentlock-browser/.venv/bin/python
```

Run each client against its own log file so the two runs do not interleave.

## Claude Desktop

Config file on this machine (it does not exist yet; create it, parent
directory included):

```
~/.config/Claude/claude_desktop_config.json
```

```json
{
  "mcpServers": {
    "elicit-probe": {
      "command": "/home/n1trolab/projects/agentlock-browser/.venv/bin/python",
      "args": ["/home/n1trolab/projects/agentlock-browser/scratchpad/elicit/server.py"],
      "env": {
        "ELICIT_LOG": "/home/n1trolab/projects/agentlock-browser/scratchpad/elicit/log_desktop.jsonl"
      }
    }
  }
}
```

Restart Claude Desktop fully after saving (quit, do not just close the
window). Confirm `elicit-probe` appears in the tools menu, then ask:

```
use confirm_action to confirm navigating to http://evil.test/x from http://fixture.test
```

## Claude Code

```bash
claude mcp add elicit-probe \
  --env ELICIT_LOG=/home/n1trolab/projects/agentlock-browser/scratchpad/elicit/log_code.jsonl \
  -- /home/n1trolab/projects/agentlock-browser/.venv/bin/python \
     /home/n1trolab/projects/agentlock-browser/scratchpad/elicit/server.py
```

Then in a Claude Code session, `/mcp` to confirm it connected, and ask the
same thing:

```
use confirm_action to confirm navigating to http://evil.test/x from http://fixture.test
```

To remove it afterwards:

```bash
claude mcp remove elicit-probe
```

## What to paste back

For each client, three things:

1. What appeared on screen. A modal dialog, an inline prompt in the
   transcript, a permission-style banner, or nothing at all. If a form
   appeared, whether the three options rendered as a dropdown, radio buttons
   or free text, and whether the message showed all three lines (action,
   value, origin).
2. What you clicked, and what the client did if you dismissed it rather than
   answering (escape key, clicking away, closing the dialog).
3. The log lines:

```bash
cat /home/n1trolab/projects/agentlock-browser/scratchpad/elicit/log_desktop.jsonl
cat /home/n1trolab/projects/agentlock-browser/scratchpad/elicit/log_code.jsonl
```

The `capability_check` line is the one that answers whether the client
advertised elicitation at all. If it says `"supported": false`, the client
never saw a form and the tool returned `{"elicitation": "unsupported"}`
without asking anything.
