# Upgrade, backup and rollback

Before changing v0.1.0 or an earlier v0.2 build, pause posting and confirm that no company
has an unresolved `in-flight`, `posted-unverified` or `unknown` job. Create a Proxmox
snapshot when storage supports it. If an LXC bind mount prevents native snapshots, run a
verified snapshot-mode `vzdump`, retain its SHA-256 and record excluded mount data.

Back up every private configuration file and each company SQLite database while services
are stopped or through the signed application backup command. Verify SQLite quick checks,
foreign keys, audit chains and the backup manifest. Restore only into an isolated path and
run read-only checks before considering the backup usable.

Install each release in `/opt/kaydbooks/releases/<version>` and move the
`/opt/kaydbooks/current` symlink only after package validation. Restart Bridge and MCP,
run the doctor and complete read-only QBWC/MCP checks. Database upgrades must preserve
prior rows and audit chains.

To roll back, pause services, restore the previous symlink and compatible private state,
restart, and run the doctor. An ambiguous accounting outcome remains held for read-only
reconciliation across rollback; never resend it. Keep the previous LXC or verified backup
until the physical Windows Web Connector has completed the new release qualification.
