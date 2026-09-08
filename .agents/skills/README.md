# Repository skills

Project-specific skills live here as `<name>/SKILL.md`, with names prefixed
`northstar-`. Add a skill only for an actual repeatable project workflow; this
directory does not copy the global skill collection or replace `AGENTS.md`.

External skills are disabled in the operator's user-level Codex configuration,
not by this directory. The current local client ignores project-level skill
exclusions when listing skills; do not add a project config that claims otherwise.
User-level exclusions affect every repository on that machine, preserve skill
files and leave plugin tools enabled. Official Codex built-in skills stay enabled
with `skills.bundled.enabled = true` and no per-name exclusions; installed external
skill names use `skills.config` exclusions.
The latter is an explicit list, not a wildcard: audit new or renamed skills when
the client/plugins change. This machine-local setting is not deployed by Git;
configure and verify it separately on the remote development host.

Existing sessions can retain already-injected instructions. Start a fresh session
at the repository root, then inspect `skills/list` (including plugin/system scopes)
to confirm no external skill remains enabled. Hosted/managed clients may inject
skills independently; local configuration is not proof of their behavior.

Verified locally on 2026-09-08 with Codex `0.151.0-alpha.7.2`: all 46 discovered
external skills disabled and six official built-ins enabled: `imagegen`,
`openai-docs`, `plugin-creator`, `review-agent`, `skill-creator`, `skill-installer`.
This describes that installed catalog, not future plugins or remote hosts.
Restart the client after changing user settings, as described in the
[official skill configuration guide](https://learn.chatgpt.com/docs/build-skills#enable-or-disable-local-codex-skills).

No project skill is installed yet. The project rules remain in `AGENTS.md`.
