"""Stdio MCP bridge for Gulliver's Hoard.

Every tool call is proxied to the running app (`POST /api/agent/call`) with the Bearer token from `<DATA_DIR>/mcp-token`; the
tool list comes from `GET /api/agent/tools`. When nothing answers, the bridge starts the app (`python -m gulliver_hoard`) and
waits for it; GULLIVER_BRIDGE_AUTOSTART=0 turns that off."""

from __future__ import annotations

import sys

from gulliver_hoard.hoard_link.bridge import CatalogBridge


def main() -> int:
    CatalogBridge(app="gulliver", service="gulliver-hoard", package="gulliver_hoard", default_port=5206, data_dir_env="GULLIVER_DATA_DIR",
                  title="Gulliver's Hoard", root=__file__, default_timeout=900.0).run_bridge()
    return 0


if __name__ == "__main__":
    sys.exit(main())
