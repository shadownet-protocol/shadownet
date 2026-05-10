// SPDX-License-Identifier: MIT

// Package snsserver is the reusable HTTP-server bootstrap for an SNS built
// atop pkg/sns.
//
// It mirrors pkg/scaserver: a thin wrapper that wires an already-configured
// *sns.Server to a hardened HTTP listener with TLS, request-id, access
// logging, and signal-driven graceful shutdown.
package snsserver
