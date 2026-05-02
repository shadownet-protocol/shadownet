// SPDX-License-Identifier: MIT

package sca

// Policy is the JSON document an SCA serves at /.well-known/sca/policy.json
// per RFC-0004 §Policy document.
type Policy struct {
	Issuer                 string        `json:"issuer"`
	Version                string        `json:"shadownet:v"`
	Levels                 []LevelPolicy `json:"levels"`
	FreshnessWindowSeconds int64         `json:"freshnessWindowSeconds"`
	StatusListBase         string        `json:"statusListBase"`
}

// LevelPolicy describes one level the SCA offers.
type LevelPolicy struct {
	Level                  string `json:"level"`
	Method                 string `json:"method"`
	RateLimit              string `json:"rateLimit,omitempty"`
	CredentialLifetimeDays int    `json:"credentialLifetimeDays"`
}

// FindLevel returns the policy entry for the given level URI.
func (p *Policy) FindLevel(level string) (LevelPolicy, bool) {
	for _, l := range p.Levels {
		if l.Level == level {
			return l, true
		}
	}
	return LevelPolicy{}, false
}
