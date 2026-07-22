@AGENTS.md

<!-- AGENT-CONFIG-PACK:CLAUDE-ADDITIONS START -->
# Claude Code additions (agent-config-pack)

> Installed/updated 2026-07-22.

- Use `/context` when instruction loading is uncertain.
- Use project Skills for repeatable multi-step procedures instead of expanding this file.
- For substantial changes, delegate final review to the `code-reviewer` subagent in a fresh context.
- Treat CLAUDE.md and auto memory as guidance, not enforcement. Mandatory checks must be implemented through tests, CI, permissions, or Hooks.
- Auto memory may store stable project discoveries only. No secrets / temp task / unverified guesses.
- Prefer `verify-work` before claiming COMPLETE.
<!-- AGENT-CONFIG-PACK:CLAUDE-ADDITIONS END -->
