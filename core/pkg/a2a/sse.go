// SPDX-License-Identifier: MIT

package a2a

import (
	"encoding/json"
	"fmt"
	"net/http"
	"time"
)

// StreamPoll is the interval at which message:stream polls the TaskStore for
// state changes. Implementations that wire push notifications can override
// it to a high value.
var StreamPoll = 500 * time.Millisecond

// StreamMaxLifetime caps how long a single message:stream connection stays
// open. Beyond this the server emits a final keepalive and closes; the
// client reconnects via task:get or another message:stream call.
const StreamMaxLifetime = 5 * time.Minute

// handleMessageStream implements RFC-0006's MUST for message:stream via SSE.
// We invoke HandlerFunc to create the task (same as message:send), then
// stream Task state changes as `data: <json>\n\n` events until the task
// reaches a terminal state, the client disconnects, or StreamMaxLifetime
// elapses.
func (s *Server) handleMessageStream(w http.ResponseWriter, r *http.Request, req *jsonrpcRequest, caller InboundCaller) {
	var p messageSendParams
	if err := json.Unmarshal(req.Params, &p); err != nil {
		writeRPCError(w, req.ID, jsonrpcInvalidRequest, "decode params: "+err.Error())
		return
	}
	if p.Message.MessageID == "" {
		p.Message.MessageID = "msg-" + newID()
	}
	task, err := s.Handler(r.Context(), caller, p.Message)
	if err != nil {
		writeRPCAppError(w, req.ID, err)
		return
	}

	flusher, ok := w.(http.Flusher)
	if !ok {
		// Fall back to a single JSON response.
		writeRPCResult(w, req.ID, task)
		return
	}

	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Connection", "keep-alive")
	w.WriteHeader(http.StatusOK)

	emit := func(t Task) bool {
		body, err := json.Marshal(jsonrpcResponse{JSONRPC: "2.0", ID: req.ID, Result: t})
		if err != nil {
			return false
		}
		if _, err := fmt.Fprintf(w, "data: %s\n\n", body); err != nil {
			return false
		}
		flusher.Flush()
		return true
	}

	if !emit(task) {
		return
	}
	if isTerminal(task.Status.State) {
		return
	}

	deadline := s.now().Add(StreamMaxLifetime)
	ticker := time.NewTicker(StreamPoll)
	defer ticker.Stop()
	last := task

	for {
		select {
		case <-r.Context().Done():
			return
		case <-ticker.C:
			if s.now().After(deadline) {
				return
			}
			t, err := s.Tasks.Get(r.Context(), task.ID)
			if err != nil {
				return
			}
			if t.Status.State == last.Status.State && len(t.History) == len(last.History) {
				continue
			}
			if !emit(t) {
				return
			}
			last = t
			if isTerminal(t.Status.State) {
				return
			}
		}
	}
}

func isTerminal(state string) bool {
	switch state {
	case TaskCompleted, TaskCanceled, TaskFailed:
		return true
	}
	return false
}
