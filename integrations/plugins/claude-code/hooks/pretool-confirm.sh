#!/bin/bash
# Shadownet plugin PreToolUse hook for social_send / social_respond.
# Adds a friction-of-attention reminder before sending a message to a peer
# Shadow over A2A — does NOT block (would deny=ask be too noisy here).
cat <<'EOF'
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "additionalContext": "About to send a message to a peer Shadow over A2A. Verify the contact_id and the content match the user's intent before continuing. Per RFC-0006, the message envelope is signed and routed by DID — there is no take-back."
  }
}
EOF
exit 0
