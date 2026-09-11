# StepAgent Terminal — user guide

## Views and navigation

The timeline groups activity by conversation turn. Each step shows its turn,
number, family, type, subtype, status, and timing. Command text and results are
shown in the details pane. Unknown protocol subtypes remain visible under their
source name; inferred actions are marked with `≈`. Messages preserve their role
and recorded channel, including progress updates and final responses.

On wide terminals (110 columns or more), the timeline and details appear side by
side. Smaller terminals open details separately. The minimum usable size is
42×12; 120×35 or larger is more comfortable. Resizing does not stop the reader.

The selected step is marked with `▶` and a high-contrast background. When focus
moves to details, the step keeps a `│` marker. The pulse at the focused pane is
an additional focus cue. Statuses distinguish waiting, blocked, failed,
cancelled, interrupted, and completed; `COMPLETED` means the step ended and does
not by itself mean the operation succeeded.

Press `/` to search steps or sessions. `Esc` clears the query and returns to
navigation; `Enter` keeps it as an active filter. Searching pauses view
following but the background reader continues. Long queries remain editable in
the narrowest supported window.

| Key | Action |
| --- | --- |
| `s` | select or switch session |
| `↑` / `↓`, `k` / `j` | select a step; scroll details when focused |
| `←` / `→` | focus timeline or details |
| `d` | expand selected step to the full pane; shrink it again |
| `Enter` | open or close full details; open a relationship target |
| `1`, `2`, `3`, `4` | content / all fields / relationships / raw sources |
| `a`, `r` | all layers / toggle raw view |
| `n`, `N` | next / previous relationship |
| `b` | return to the previous relationship context |
| `u` | refresh pinned details after new data arrives |
| `Space`, `f` | follow new steps / browse history |
| `g` / `G` | first step / newest step and follow |
| `PgUp` / `PgDn` | scroll one page |
| `[` / `]` | previous / next turn |
| `t` | selected turn only / all turns |
| `/` | search steps or sessions |
| `Tab`, `m` | timeline / activity matrix |
| `Esc` | go back and clear filters |
| `?` | help |
| `q`, `Ctrl+C` | quit and restore the terminal |

The activity matrix counts steps by category and turn under the current filters.
New records continue to appear while browsing history, searching, or selecting
a session. A result with the same `call_id` updates its existing call step.

## Detail layers

Layer `1` presents semantic content for messages, analysis, commands, diffs,
plans, questions, token usage, MCP, web, agents, media, and protocol events.
Commands show arguments, stdout, stderr, and exit code. Diffs show files,
operations, hunk locations, line numbers, and additions/deletions. `exec`
wrappers show their literal arguments, script, parameters, and nested outputs.

Layer `2` shows every source field separately with its JSON Pointer and value
type. False, zero, null, empty strings, arrays, and objects remain visible;
normalization is shown separately and does not replace source values.

Layer `3` shows relationship direction, target, kind, confidence, chronology,
and whether each link is explicit or inferred. You can open a session, agent,
turn, plan task, artifact, or graph error without adding it to the timeline.
The navigation history keeps the last 32 transitions.

Layer `4` shows complete parsed JSON values for all records assigned to the step,
in source order. It is formatted JSON, not a byte-for-byte copy of the JSONL
line. Control characters are escaped and are never sent to the terminal.

The `a` key displays all layers in sequence. Positions are remembered per step
and layer. A `30+` count means more rows are available while scrolling. Opening
details pins a snapshot; press `u` to refresh it. When new data arrives, the
status bar shows `NEW SESSION DATA: u`.

## Live reading and safety

The reader polls every 250 ms by default and consumes only appended bytes in a
background thread. This is observation of a local JSONL file, not a model token
stream; upstream buffering affects freshness. `LIVE` means that the view follows
new steps. Recent file activity does not prove that Codex is still running.

Calls and results are correlated by recorded `call_id` or item identifiers.
Record order and the `next` relation describe write order, not proof of
causality. The parser skips initialization before the first user prompt while
preserving all later protocol and unknown records.

Incomplete lines wait for a newline, including split UTF-8 bytes. Malformed JSON
or non-object records are skipped with a visible count. Truncation or inode
replacement rebuilds the state; a missing file is retried. An in-place edit that
keeps the same size and inode is not detected, so reselect that session.

The application is local and read-only. It never executes commands found in
logs, writes to the Codex directory, or sends data over the network. Session
logs can contain private prompts and tool arguments; review them before sharing
screenshots or files.
