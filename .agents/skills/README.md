# Repository skills

These project workflows are maintained with the code, not installed globally:

- `northstar-issue-delivery`: reconcile Issue/Project evidence, implement the next
  vertical result and publish accurate acceptance status.
- `northstar-runtime-verification`: choose existing installation, persistence and
  lifecycle checks without confusing local tests with browser or broker evidence.

They provide repository-specific navigation and operational knowledge for the
current Astra workflow, not a generic reasoning framework or a claim of measured
model improvement. `AGENTS.md` remains the source of engineering and authorization
rules. Add a skill only when a repeated project task benefits from instructions
not already clear in code or those rules; keep descriptions narrow.

OpenAI official built-in and plugin skills remain client-managed and enabled.
The former live in the client's `.codex/skills/.system/`; they are not vendored here.
User-installed global development skills were removed from this Mac's discovery
directory to a recoverable Trash copy. That copy is not another maintained skill set.
Non-OpenAI plugin skill exclusions remain an operator setting; plugin tools are
unchanged. Determine authorship from the plugin manifest, not its marketplace name.

Start a fresh Codex session at the repository root and inspect `skills/list` to
verify local skills plus official skills are enabled. User-level client settings
and filesystem cleanup are machine-local: pulling Git on a remote development
host delivers these skills, but does not remove that host's global installations.
Already-injected or host-managed session instructions cannot be unloaded by this
directory. Maintain skills as small text changes alongside affected workflows;
there is no additional service, generated registry or document-test suite.
