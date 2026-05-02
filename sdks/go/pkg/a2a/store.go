// SPDX-License-Identifier: MIT

package a2a

import (
	"context"
	"errors"
	"sync"
)

// ErrTaskNotFound is returned by TaskStore.Get when the id is unknown.
var ErrTaskNotFound = errors.New("a2a: task not found")

// TaskStore persists Tasks. Implementations are responsible for thread-safe
// reads and writes.
type TaskStore interface {
	Get(ctx context.Context, id string) (Task, error)
	Put(ctx context.Context, t Task) error
	Cancel(ctx context.Context, id string) (Task, error)
}

// MemoryTaskStore is an in-memory TaskStore suitable for the reference
// servers and for tests.
type MemoryTaskStore struct {
	mu    sync.RWMutex
	tasks map[string]Task
}

// NewMemoryTaskStore returns an empty store.
func NewMemoryTaskStore() *MemoryTaskStore { return &MemoryTaskStore{tasks: make(map[string]Task)} }

// Get implements TaskStore.
func (s *MemoryTaskStore) Get(_ context.Context, id string) (Task, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	t, ok := s.tasks[id]
	if !ok {
		return Task{}, ErrTaskNotFound
	}
	return t, nil
}

// Put implements TaskStore.
func (s *MemoryTaskStore) Put(_ context.Context, t Task) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.tasks[t.ID] = t
	return nil
}

// Cancel implements TaskStore. Idempotent: cancelling an already-canceled
// task is a no-op.
func (s *MemoryTaskStore) Cancel(_ context.Context, id string) (Task, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	t, ok := s.tasks[id]
	if !ok {
		return Task{}, ErrTaskNotFound
	}
	if t.Status.State != TaskCompleted && t.Status.State != TaskFailed {
		t.Status.State = TaskCanceled
		s.tasks[id] = t
	}
	return t, nil
}
