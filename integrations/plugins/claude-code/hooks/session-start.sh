#!/bin/bash
# Shadownet plugin SessionStart hook.
# Emits a single line of additionalContext so Claude knows the plugin is loaded
# and the right skills are available — no MCP calls (hooks shouldn't recurse).
cat <<'EOF'
{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "Shadownet plugin loaded. Skills: /shadownet:shadownet-setup, /shadownet:shadownet-reach-out, /shadownet:shadownet-inbox, /shadownet:shadownet-coordinate. Run shadownet-setup once on first install to verify the connection."
  }
}
EOF
exit 0
