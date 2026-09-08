# Repository skills

Project-specific skills live here as `<name>/SKILL.md`, with names prefixed
`northstar-`. Add a skill only for an actual repeatable project workflow; this
directory does not copy the global skill collection or replace `AGENTS.md`.

Non-OpenAI global skills are disabled in the operator's user-level Codex configuration,
not by this directory. The current local client ignores project-level skill
exclusions when listing skills; do not add a project config that claims otherwise.
User-level exclusions affect every repository on that machine, preserve skill
files and leave plugin tools enabled. Official Codex built-in skills stay enabled
with `skills.bundled.enabled = true`. OpenAI-authored plugin skills also remain
enabled: plugin-provided does not mean third-party. Check the plugin manifest's
author rather than inferring authorship from its installation scope or marketplace.
Non-OpenAI global skill names use `skills.config` exclusions.
The latter is an explicit list, not a wildcard: audit new or renamed skills when
the client/plugins change. This machine-local setting is not deployed by Git;
configure and verify it separately on the remote development host.

Existing sessions can retain already-injected instructions. Start a fresh session
at the repository root, then inspect `skills/list` (including plugin/system scopes)
to confirm official skills remain enabled and excluded skills remain disabled.
Hosted/managed clients may inject
skills independently; local configuration is not proof of their behavior.

Verified locally on 2026-09-08 with Codex `0.151.0-alpha.7.2`: 37 user-installed
development skills disabled; nine OpenAI plugin skills and six official built-ins
enabled. OpenAI Deep Research and Plugin Management exclusions were also removed;
those skills were not in that local client's discovered catalog. Figma-authored
skills remain excluded, distinct from OpenAI-authored plugins.
This describes that installed catalog, not future plugins or remote hosts.
Restart the client after changing user settings, as described in the
[official skill configuration guide](https://learn.chatgpt.com/docs/build-skills#enable-or-disable-local-codex-skills).

No project skill is installed yet. The project rules remain in `AGENTS.md`.
