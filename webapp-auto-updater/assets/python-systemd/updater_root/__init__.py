"""Root-only privileged updater package.

This package is imported ONLY from the __APP_SLUG__-updater.service
systemd unit, which runs as root. The main service (__APP_SLUG__.service,
User=__SERVICE_USER__) MUST NEVER import from this package. The trust boundary is
filesystem-enforced and grep-verifiable.

Allowed imports from __PKG_NAME__.*:
    - releases     (read-only constants + layout helpers)
    - recovery     (PendingMarker schema + path constants)
    - state_file

Forbidden:
    - webapp, __main__, context, and any app-specific runtime modules,
      updater.*
"""
UPDATER_ROOT_SCHEMA_VERSION = 1
