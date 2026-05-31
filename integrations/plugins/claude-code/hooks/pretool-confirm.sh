#!/bin/bash
# Shadownet plugin PreToolUse hook for mcp_shadownet_send / mcp_shadownet_respond.
# Adds a friction-of-attention reminder before sending a message to a peer
# Shadow over A2A — does NOT block (deny=ask would be too noisy here).
cat <<'EOF'
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "additionalContext": "About to send a message to a peer Shadow over A2A. Verify the recipient and content match the user's intent before continuing. Per RFC 0001 §8, the envelope is signed and bound to (from, to, msgHash) — there is no take-back."
  }
}
EOF
exit 0
