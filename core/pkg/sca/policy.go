// SPDX-License-Identifier: MIT

package sca

// Mode tells the Issuer which credential families it is configured to mint.
// RFC-0004 §Enterprise SCAs allows a single SCA binary to serve as a
// personhood SCA, an affiliation SCA, or both.
type Mode string

// Mode values.
const (
	ModePersonhood  Mode = "personhood"
	ModeAffiliation Mode = "affiliation"
	ModeBoth        Mode = "both"
)

// Policy is the JSON document an SCA serves at /.well-known/sca/policy.json
// per RFC-0004 §Policy document.
type Policy struct {
	Issuer                 string        `json:"issuer"`
	Version                string        `json:"shadownet:v"`
	Mode                   Mode          `json:"mode,omitempty"`
	Levels                 []LevelPolicy `json:"levels,omitempty"`
	FreshnessWindowSeconds int64         `json:"freshnessWindowSeconds"`
	StatusListBase         string        `json:"statusListBase"`

	// Affiliation fields, populated when Mode is "affiliation" or "both".
	AffiliationOrg                    string `json:"affiliationOrg,omitempty"`
	AffiliationFreshnessWindowSeconds int64  `json:"affiliationFreshnessWindowSeconds,omitempty"`
	AffiliationLifetimeDays           int    `json:"affiliationLifetimeDays,omitempty"`
	AffiliationStatusListBase         string `json:"affiliationStatusListBase,omitempty"`
}

// LevelPolicy describes one level the SCA offers.
type LevelPolicy struct {
	Level                  string `json:"level"`
	Method                 string `json:"method"`
	RateLimit              string `json:"rateLimit,omitempty"`
	CredentialLifetimeDays int    `json:"credentialLifetimeDays"`
}

// EffectiveMode returns Policy.Mode, defaulting to ModePersonhood for
// backward compatibility with config that predates Mode.
func (p *Policy) EffectiveMode() Mode {
	if p.Mode == "" {
		return ModePersonhood
	}
	return p.Mode
}

// IssuesPersonhood reports whether the SCA's policy enables SubjectCredential
// issuance.
func (p *Policy) IssuesPersonhood() bool {
	m := p.EffectiveMode()
	return m == ModePersonhood || m == ModeBoth
}

// IssuesAffiliation reports whether the SCA's policy enables
// AffiliationCredential issuance.
func (p *Policy) IssuesAffiliation() bool {
	m := p.EffectiveMode()
	return m == ModeAffiliation || m == ModeBoth
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
