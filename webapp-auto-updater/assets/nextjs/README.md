# Next.js asset stubs

Placeholder scaffolding for the Node/Next.js stack. The hard logic (SHA-ancestor gate,
blue-green checkout, healthcheck + rollback, the systemd cgroup-coupling trap) is identical
to the Python stack — see ../../references/nextjs-notes.md and ../../references/architecture.md.
Port the Python primitives from ../python-systemd/ here, swapping pip/venv for `npm ci && npm run build`
and the restart for `pm2 reload` or `systemctl restart`.
