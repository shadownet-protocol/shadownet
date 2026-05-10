// SPDX-License-Identifier: MIT

// Package config loads YAML configuration with single-level env-var overrides.
//
// Env override convention: a section.key in YAML can be overridden by setting
// SHADOWNET_<SECTION>_<KEY> (uppercased, joined by underscore). Only the
// section/key forms documented per binary are honored — unrelated env vars
// are ignored.
package config

import (
	"fmt"
	"os"
	"strconv"
	"strings"

	"gopkg.in/yaml.v3"
)

// Load reads the YAML config at path and decodes it into out. Out MUST be a
// pointer to a struct annotated with `yaml` tags.
func Load(path string, out any) error {
	raw, err := os.ReadFile(path)
	if err != nil {
		return fmt.Errorf("config: read %s: %w", path, err)
	}
	if err := yaml.Unmarshal(raw, out); err != nil {
		return fmt.Errorf("config: parse %s: %w", path, err)
	}
	return nil
}

// EnvString returns the env var value if set, else the default.
func EnvString(key, def string) string {
	if v, ok := os.LookupEnv(key); ok {
		return v
	}
	return def
}

// EnvInt returns the env var value parsed as int if set, else def.
func EnvInt(key string, def int) (int, error) {
	v, ok := os.LookupEnv(key)
	if !ok {
		return def, nil
	}
	n, err := strconv.Atoi(strings.TrimSpace(v))
	if err != nil {
		return 0, fmt.Errorf("config: env %s = %q: %w", key, v, err)
	}
	return n, nil
}
