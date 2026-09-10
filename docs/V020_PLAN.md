# KaydBooks Bridge v0.2.0 execution plan

The controlling scope is the operator request dated 2026-09-10. v0.3.0 SaaS,
billing, subscriptions and permanent customer domains are excluded.

## Evidence and release gate

`v020-acceptance.json` is the canonical set of 39 gates. A passing synthetic test
does not qualify live deployment. Merge requires all preceding gates; the final
merge gate is recorded only after the qualified branch lands on main. Publishing
remains disabled by the repository release workflow.

## Audit record

- Codex: unrestricted filesystem, network enabled, approval policy never. This
  does not grant credentials or override OS and service authentication.
- Windows: this physical host runs Codex, QuickBooks Desktop and QBWC. It is not a
  Proxmox VM. The Proxmox host is a separate physical computer.
- Repository: started at 73c2888 on codex/foundation. Four modified tracked files
  and three untracked inventory worksheet files preserved with SHA256 manifest,
  binary patch and verified full Git bundle outside Git. No AGENTS.md found in
  the workspace or ancestor directories; global Codex AGENTS.md is empty.
- GitHub: origin read, authenticated push, draft PR and Windows/Linux CI are verified.
- Hermes: the gateway and KaydBooks worker are active in the new Debian 13 LXC under
  dedicated restricted accounts. The prior LXC remains stopped as rollback evidence.
- Proxmox: the separate physical host is administered through the verified
  `kaydbooks-proxmox` SSH alias. The new LXC is reached through `kaydbooks-lxc`;
  its storage, network and snapshot qualification have passed.
- Current Bridge, Remote MCP and Hermes runtimes are in the new Linux LXC. Private
  configuration and credentials remain outside Git. Windows retains only the local
  Codex operator, QuickBooks Desktop, QBWC and private connector material.
- The release candidate has per-company state, durable QBWC, authenticated HTTP MCP
  with retained stdio compatibility, and restricted new-user defaults. Historical
  v0.1.0 scores remain earlier-pilot evidence and do not qualify v0.2.0.

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

Available and verified: repository, local shell, the physical Windows workstation,
Proxmox and LXC SSH aliases, Tailscale private HTTPS, Linux services, Remote MCP,
Hermes WhatsApp, GitHub push and CI. The ChatGPT principal, Secure MCP Tunnel client
and draft configuration are prepared; creating its persistent tunnel credentials and
app remains an authenticated human checkpoint. QuickBooks Add Application approval and
the live sample cycle are also human checkpoints. No production company is authorized
for posting by this request.
