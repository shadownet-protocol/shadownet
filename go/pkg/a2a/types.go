// SPDX-License-Identifier: MIT

package a2a

import (
	"encoding/json"
	"time"
)

// Method names per A2A v1.0 §JSON-RPC methods.
const (
	MethodMessageSend   = "message:send"
	MethodMessageStream = "message:stream"
	MethodTaskGet       = "task:get"
	MethodTaskCancel    = "task:cancel"
)

// Task lifecycle states. We carry the canonical A2A v1.0 set; Shadownet
// peers add no states of their own.
const (
	TaskSubmitted     = "submitted"
	TaskWorking       = "working"
	TaskInputRequired = "input-required"
	TaskCompleted     = "completed"
	TaskCanceled      = "canceled"
	TaskFailed        = "failed"
)

// Role values for A2A messages.
const (
	RoleUser  = "user"
	RoleAgent = "agent"
)

// PartType discriminates the contents of a Part. Shadownet introduces one
// type ("shadownet/v1+envelope"); A2A defines Text/File/Data which we accept
// as opaque.
const (
	PartShadownetEnvelope = "shadownet/v1+envelope"
	PartText              = "text"
	PartFile              = "file"
	PartData              = "data"
)

// Part is a single component of an A2A message.
//
// Concretely we marshal one of {Text, MediaType, Data} based on Type. The
// shadownet/v1+envelope part carries Shadownet's structured payload via
// the Data field decoded into Envelope.
type Part struct {
	Type      string          `json:"type"`
	MediaType string          `json:"mediaType,omitempty"`
	Text      string          `json:"text,omitempty"`
	Data      json.RawMessage `json:"data,omitempty"`
}

// Message is an A2A v1.0 message.
type Message struct {
	Role      string `json:"role"`
	MessageID string `json:"messageId"`
	TaskID    string `json:"taskId,omitempty"`
	ContextID string `json:"contextId,omitempty"`
	Parts     []Part `json:"parts"`
}

// TaskStatus is the state of a Task at a point in time.
type TaskStatus struct {
	State     string    `json:"state"`
	Message   *Message  `json:"message,omitempty"`
	Timestamp time.Time `json:"timestamp"`
}

// Task is an A2A v1.0 Task.
type Task struct {
	ID        string     `json:"id"`
	ContextID string     `json:"contextId,omitempty"`
	Status    TaskStatus `json:"status"`
	History   []Message  `json:"history,omitempty"`
}
