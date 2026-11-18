"""`python -m agent` starts the MCP server on stdio (same as `python -m agent.server`)."""

import sys

from agent.server import main

sys.exit(main())
