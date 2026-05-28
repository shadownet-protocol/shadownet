// SPDX-License-Identifier: MIT

package vc

import (
	"encoding/json"
	"errors"
	"fmt"
)

// MaxPredicateDepth is the cap from RFC-0004 §Required-level predicates.
// Predicates deeper than this MUST be rejected as `predicate_too_deep`.
const MaxPredicateDepth = 4

// ErrPredicateTooDeep mirrors RFC-0004's `predicate_too_deep` error code.
var ErrPredicateTooDeep = errors.New("vc: predicate exceeds maximum depth")

// Predicate is the recursive expression from RFC-0004 §Required-level
// predicates. Exactly one of the fields is set on a valid predicate.
type Predicate struct {
	Level       string       `json:"level,omitempty"`
	Issuer      string       `json:"issuer,omitempty"`
	SubjectType string       `json:"subjectType,omitempty"`
	All         []*Predicate `json:"all,omitempty"`
	Any         []*Predicate `json:"any,omitempty"`
	Not         *Predicate   `json:"not,omitempty"`
}

// ParsePredicate decodes a predicate from JSON and validates depth + shape.
func ParsePredicate(raw []byte) (*Predicate, error) {
	var p Predicate
	if err := json.Unmarshal(raw, &p); err != nil {
		return nil, fmt.Errorf("vc: parse predicate: %w", err)
	}
	if err := p.validate(0); err != nil {
		return nil, err
	}
	return &p, nil
}

func (p *Predicate) validate(depth int) error {
	if depth > MaxPredicateDepth {
		return ErrPredicateTooDeep
	}
	set := 0
	if p.Level != "" {
		set++
	}
	if p.Issuer != "" {
		set++
	}
	if p.SubjectType != "" {
		set++
	}
	if p.All != nil {
		set++
	}
	if p.Any != nil {
		set++
	}
	if p.Not != nil {
		set++
	}
	if set != 1 {
		return fmt.Errorf("vc: predicate must set exactly one of leaf/all/any/not (got %d set fields)", set)
	}
	switch {
	case p.SubjectType != "":
		if SubjectType(p.SubjectType) != SubjectPerson && SubjectType(p.SubjectType) != SubjectOrganization {
			return fmt.Errorf("vc: predicate subjectType = %q, must be person or organization", p.SubjectType)
		}
	case p.All != nil:
		if len(p.All) == 0 {
			return errors.New("vc: predicate all requires ≥1 child")
		}
		for _, c := range p.All {
			if err := c.validate(depth + 1); err != nil {
				return err
			}
		}
	case p.Any != nil:
		if len(p.Any) == 0 {
			return errors.New("vc: predicate any requires ≥1 child")
		}
		for _, c := range p.Any {
			if err := c.validate(depth + 1); err != nil {
				return err
			}
		}
	case p.Not != nil:
		if err := p.Not.validate(depth + 1); err != nil {
			return err
		}
	}
	return nil
}

// Match evaluates p against a set of validated credentials per RFC-0004.
// Each leaf is satisfied if at least one credential matches.
func (p *Predicate) Match(creds []*SubjectCredential) bool {
	switch {
	case p.Level != "":
		for _, c := range creds {
			if c.Level == p.Level {
				return true
			}
		}
		return false
	case p.Issuer != "":
		for _, c := range creds {
			if c.Issuer == p.Issuer {
				return true
			}
		}
		return false
	case p.SubjectType != "":
		for _, c := range creds {
			if string(c.SubjectType) == p.SubjectType {
				return true
			}
		}
		return false
	case p.All != nil:
		for _, child := range p.All {
			if !child.Match(creds) {
				return false
			}
		}
		return true
	case p.Any != nil:
		for _, child := range p.Any {
			if child.Match(creds) {
				return true
			}
		}
		return false
	case p.Not != nil:
		return !p.Not.Match(creds)
	}
	return false
}
