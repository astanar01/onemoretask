## Memory — Muninn

Muninn (`mcp__muninn__*` tools, MuninnDB) is the long-term memory across sessions. A per-prompt hook
carries the recall/remember reminder. When working on a feature area, recall key decisions with tag
`key-decision`.

**Recall with `mode: "semantic"` and `threshold: 0.5`.** That cutoff returns nothing when nothing
relevant is stored, so an empty result is a real "no memory", not a failure. Semantic recall needs
embeddings; local Ollama with `nomic-embed-text`, set in `~/.muninn/muninn.env`, is optional.

### If Muninn is unreachable, fix it — don't shrug it off

Claude talks to `muninn mcp`, a stdio proxy; the proxy talks to the Muninn daemon on port 8750. The
proxy can be running while the daemon is down.

1. **Diagnose**: run `muninn status`, or call `mcp__muninn__muninn_status`.
2. **Restart**: `muninn start` if the daemon is down, `muninn restart` if it is up but not answering.
   Then check `muninn status` again.
3. **Persist**: if the daemon keeps dying, read its log for the root cause and fix that. Don't restart in
   a loop.
4. **"Up" is not "working"**: status can look good while every recall returns nothing. Also recall a
   memory you know is stored and see it come back.
5. **Then save the memories you tried to write.** A failed `muninn_remember` is lost unless you retry it.
