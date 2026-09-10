# KaydBooks Bridge v0.2.0 execution plan

The controlling scope is the operator request dated 2026-09-10. v0.3.0 SaaS,
billing, subscriptions and permanent customer domains are excluded.

## Evidence and release gate

`v020-acceptance.json` is the canonical set of 39 gates. A passing synthetic test
does not qualify live deployment. Merge requires all preceding gates; the final
merge gate is recorded only after the qualified branch lands on main. Publishing
remains disabled by the repository release workflow.

## Initial audit

- Codex: unrestricted filesystem, network enabled, approval policy never. This
  does not grant credentials or override OS and service authentication.
- Windows: physical QuickBooks host; QuickBooks services and QBWC running.
- Repository: started at 73c2888 on codex/foundation. Four modified tracked files
  and three untracked inventory worksheet files preserved with SHA256 manifest,
  binary patch and verified full Git bundle outside Git. No AGENTS.md found in
  the workspace or ancestor directories; global Codex AGENTS.md is empty.
- GitHub: origin read succeeds. Push and CI still require verification.
- Hermes: SSH succeeds to existing Debian 13 LXC; gateway and KaydBooks worker
  active. Runs as root today; v0.2.0 requires a dedicated restricted account.
- Proxmox: online in Tailscale. Direct SSH refuses an unknown host key. No
  verified Proxmox SSH identity has yet been found; snapshot/storage/network
  inventory remains pending. Do not weaken SSH trust checks.
- Current Bridge runtime is on Windows. Configuration and credentials reside
  outside Git. No runtime or accounting changes have been made.
- Existing code has per-company state, durable QBWC and stdio MCP. New-user
  permissions default broadly and must become restricted. Historical documents
  disagree on v0.1.0 scores; those are not v0.2.0 acceptance evidence.

## Execution order

1. Complete runtime/access inventory; record credential or human checkpoints.
2. Snapshot existing Hermes LXC before material migration; create a fresh LXC.
3. Implement authenticated HTTP control plane, source/tool restrictions,
   restricted user defaults and expiring audited owner grants.
4. Add repeatable Linux service installation, routing, company onboarding,
   health checks and backup/isolated restore/migration tooling.
5. Qualify transport, isolation, revocation, owner expiry and safety regression.
6. Migrate copied state with writes paused; qualify private HTTPS and QBWC from
   the physical Windows server. Keep original Hermes available for rollback.
7. Qualify the authorized sample through exact previews and approval, readback,
   duplicate/interruption recovery, WhatsApp delivery and client connections.
8. Build and clean-install artifact, run Windows/Linux CI, inspect complete diff,
   push, and merge only after every required qualification has evidence.

## Access classification

Available now: repository, local shell, Windows service inspection, Hermes SSH,
Tailscale inventory, GitHub read. Approval is not presently a tool restriction.
Missing/unverified: trusted Proxmox login, snapshot capability, fresh LXC,
ChatGPT authenticated client connection, neutral HTTPS endpoint.
Human-only if encountered: MFA, WhatsApp QR, QuickBooks Add Application/license.
No production company is authorized for posting by this request.
