// SPDX-License-Identifier: MIT

package storemem

import (
	"context"
	"strings"
	"sync"

	"github.com/shadownet-protocol/shadownet/core/pkg/sns"
)

// SNSRecordStore is an in-memory sns.RecordStore. Local keys are
// canonicalized to lowercase.
type SNSRecordStore struct {
	mu         sync.Mutex
	records    map[string]sns.Record
	tombstones map[string]struct{}
}

// NewSNSRecordStore returns an empty in-memory record store.
func NewSNSRecordStore() *SNSRecordStore {
	return &SNSRecordStore{
		records:    make(map[string]sns.Record),
		tombstones: make(map[string]struct{}),
	}
}

// Get implements sns.RecordStore.
func (s *SNSRecordStore) Get(_ context.Context, local string) (sns.Record, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	key := strings.ToLower(local)
	if _, ok := s.tombstones[key]; ok {
		return sns.Record{}, sns.ErrRecordTombstoned
	}
	rec, ok := s.records[key]
	if !ok {
		return sns.Record{}, sns.ErrRecordNotFound
	}
	return rec, nil
}

// Put implements sns.RecordStore.
func (s *SNSRecordStore) Put(_ context.Context, r sns.Record) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	parts := strings.SplitN(r.Shadowname, "@", 2)
	if len(parts) != 2 {
		return sns.ErrRecordNotFound
	}
	key := strings.ToLower(parts[0])
	delete(s.tombstones, key)
	s.records[key] = r
	return nil
}

// Delete implements sns.RecordStore.
func (s *SNSRecordStore) Delete(_ context.Context, local string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	key := strings.ToLower(local)
	delete(s.records, key)
	s.tombstones[key] = struct{}{}
	return nil
}
