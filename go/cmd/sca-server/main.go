// SPDX-License-Identifier: MIT

// Command sca-server is the reference Shadow Certificate Authority HTTP
// server. It implements the RFC-0004 endpoints atop pkg/sca, and ships a
// single ProofMethod — InstantApprovalProofMethod — for local development.
//
// SMTP, Stripe Identity, biometric kiosks, and other production proof
// methods belong in operator deployments, not in this binary.
package main

import (
	"context"
	"crypto/tls"
	"database/sql"
	"errors"
	"flag"
	"fmt"
	"log/slog"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"github.com/shadownet-protocol/shadownet/go/internal/config"
	"github.com/shadownet-protocol/shadownet/go/pkg/crypto"
	"github.com/shadownet-protocol/shadownet/go/pkg/did"
	"github.com/shadownet-protocol/shadownet/go/pkg/httpx"
	"github.com/shadownet-protocol/shadownet/go/pkg/keyguard"
	"github.com/shadownet-protocol/shadownet/go/pkg/sca"
	"github.com/shadownet-protocol/shadownet/go/pkg/scaserver"
	"github.com/shadownet-protocol/shadownet/go/pkg/storemem"
	"github.com/shadownet-protocol/shadownet/go/pkg/vc"
)

// fileConfig is the YAML schema cmd/sca-server consumes.
type fileConfig struct {
	DID    string `yaml:"did"`
	Listen string `yaml:"listen"`
	TLS    struct {
		Cert string `yaml:"cert"`
		Key  string `yaml:"key"`
	} `yaml:"tls"`
	Signing struct {
		KeyFile string `yaml:"keyfile"`
	} `yaml:"signing"`
	Storage struct {
		Driver string `yaml:"driver"` // "memory" | "sqlite"
		DSN    string `yaml:"dsn"`
	} `yaml:"storage"`
	Policy fileConfigPolicy `yaml:"policy"`
}

type fileConfigPolicy struct {
	Levels                 []sca.LevelPolicy `yaml:"levels"`
	FreshnessWindowSeconds int64             `yaml:"freshnessWindowSeconds"`
	StatusListBase         string            `yaml:"statusListBase"`
}

// version is stamped at build time by the release pipeline via
// `-ldflags "-X main.version=$tag"`. Local builds keep the "dev" sentinel.
var version = "dev"

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, "sca-server: "+err.Error())
		os.Exit(1)
	}
}

func run() error {
	fs := flag.NewFlagSet("sca-server", flag.ContinueOnError)
	configPath := fs.String("config", "", "path to YAML config file")
	logLevel := fs.String("log-level", config.EnvString("SHADOWNET_LOG_LEVEL", "info"), "log level: debug|info|warn|error")
	showVersion := fs.Bool("version", false, "print version and exit")
	if err := fs.Parse(os.Args[1:]); err != nil {
		return err
	}
	if *showVersion {
		fmt.Printf("sca-server %s\n", version)
		return nil
	}
	if *configPath == "" {
		return errors.New("--config is required")
	}

	logger := newLogger(*logLevel, config.EnvString("SHADOWNET_LOG_FORMAT", ""))
	slog.SetDefault(logger)

	var cfg fileConfig
	if err := config.Load(*configPath, &cfg); err != nil {
		return err
	}
	applyEnvOverrides(&cfg)
	if err := validateConfig(&cfg); err != nil {
		return err
	}

	kp, err := crypto.LoadKeyFile(cfg.Signing.KeyFile)
	if err != nil {
		return fmt.Errorf("load signing key: %w (generate one with `shadownet keygen`)", err)
	}
	if err := keyguard.AssertNotFixture(kp.Public, "sca-server"); err != nil {
		return err
	}
	keyID := cfg.DID + "#" + sca.DefaultListID

	policy := sca.Policy{
		Issuer:                 cfg.DID,
		Version:                vc.Version,
		Levels:                 cfg.Policy.Levels,
		FreshnessWindowSeconds: cfg.Policy.FreshnessWindowSeconds,
		StatusListBase:         cfg.Policy.StatusListBase,
	}
	if policy.FreshnessWindowSeconds == 0 {
		policy.FreshnessWindowSeconds = int64(vc.MaxFreshnessLifetime / time.Second)
	}
	if policy.StatusListBase == "" {
		return errors.New("policy.statusListBase required")
	}

	sessions, issuance, revocation, db, err := openStores(cfg.Storage.Driver, cfg.Storage.DSN)
	if err != nil {
		return err
	}
	if db != nil {
		defer func() {
			if cerr := db.Close(); cerr != nil {
				logger.Warn("storage close", slog.String("err", cerr.Error()))
			}
		}()
	}

	issuer := &sca.Issuer{
		DID:        cfg.DID,
		KeyID:      keyID,
		Key:        kp,
		Resolver:   buildResolver(cfg.DID),
		Sessions:   sessions,
		Issuance:   issuance,
		Revocation: revocation,
		Methods: map[string]sca.ProofMethod{
			scaserver.InstantApproval: scaserver.InstantApprovalProofMethod{},
		},
		Policy:     policy,
		ReadyCheck: readyCheck(db),
		Caller:     &sca.HTTPCaller{Logger: logger},
	}
	if err := issuer.Validate(); err != nil {
		return err
	}

	if err := scaserver.AssertInstantApprovalNotPublic(logger, cfg.Listen, cfg.Policy.Levels); err != nil {
		return err
	}

	tlsCfg, err := buildTLS(cfg)
	if err != nil {
		return err
	}

	logger.Info(
		"starting sca-server",
		slog.String("version", version),
		slog.String("did", cfg.DID),
		slog.String("storage", cfg.Storage.Driver),
	)

	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	return scaserver.Run(ctx, scaserver.RunConfig{
		Issuer: issuer,
		Listen: cfg.Listen,
		TLS:    tlsCfg,
		Logger: logger,
	})
}

func validateConfig(cfg *fileConfig) error {
	if cfg.DID == "" {
		return errors.New("did required")
	}
	if cfg.Listen == "" {
		return errors.New("listen required")
	}
	if cfg.Signing.KeyFile == "" {
		return errors.New("signing.keyfile required")
	}
	if cfg.Storage.Driver == "" {
		cfg.Storage.Driver = "memory"
	}
	switch cfg.Storage.Driver {
	case "memory", "sqlite":
	default:
		return fmt.Errorf("storage.driver must be memory or sqlite (got %q)", cfg.Storage.Driver)
	}
	if cfg.Storage.Driver == "sqlite" && cfg.Storage.DSN == "" {
		return errors.New("storage.dsn required when driver = sqlite")
	}
	if len(cfg.Policy.Levels) == 0 {
		return errors.New("policy.levels must contain ≥1 entry")
	}
	for i, l := range cfg.Policy.Levels {
		if l.Level == "" || l.Method == "" || l.CredentialLifetimeDays <= 0 {
			return fmt.Errorf("policy.levels[%d] missing level, method, or credentialLifetimeDays", i)
		}
	}
	return nil
}

func applyEnvOverrides(cfg *fileConfig) {
	cfg.DID = config.EnvString("SHADOWNET_DID", cfg.DID)
	cfg.Listen = config.EnvString("SHADOWNET_LISTEN", cfg.Listen)
	cfg.TLS.Cert = config.EnvString("SHADOWNET_TLS_CERT", cfg.TLS.Cert)
	cfg.TLS.Key = config.EnvString("SHADOWNET_TLS_KEY", cfg.TLS.Key)
	cfg.Signing.KeyFile = config.EnvString("SHADOWNET_SIGNING_KEYFILE", cfg.Signing.KeyFile)
	cfg.Storage.Driver = config.EnvString("SHADOWNET_STORAGE_DRIVER", cfg.Storage.Driver)
	cfg.Storage.DSN = config.EnvString("SHADOWNET_STORAGE_DSN", cfg.Storage.DSN)
}

// openStores returns the SCA store trio plus an optional *sql.DB to close on
// shutdown (nil for the in-memory driver).
func openStores(driver, dsn string) (sca.SessionStore, sca.IssuanceStore, sca.RevocationStore, *sql.DB, error) {
	switch driver {
	case "memory":
		return storemem.NewSCASessionStore(),
			storemem.NewSCAIssuanceStore(),
			storemem.NewSCARevocationStore(sca.DefaultListID),
			nil,
			nil
	case "sqlite":
		return openSQLiteStores(dsn)
	default:
		return nil, nil, nil, nil, fmt.Errorf("unknown storage driver %q", driver)
	}
}

func readyCheck(db *sql.DB) func(context.Context) error {
	if db == nil {
		return nil
	}
	return func(ctx context.Context) error {
		ctx, cancel := context.WithTimeout(ctx, 2*time.Second)
		defer cancel()
		return db.PingContext(ctx)
	}
}

// buildResolver returns a DID resolver. did:web SCAs need WebResolver to
// validate subject-auth and CSR signatures; did:key SCAs (test/dev) don't.
func buildResolver(scaDID string) did.Resolver {
	if strings.HasPrefix(scaDID, "did:web:") {
		return did.NewResolver(did.NewWebResolver())
	}
	return did.NewResolver(nil)
}

func buildTLS(cfg fileConfig) (*tls.Config, error) {
	if cfg.TLS.Cert == "" && cfg.TLS.Key == "" {
		return nil, nil
	}
	if cfg.TLS.Cert == "" || cfg.TLS.Key == "" {
		return nil, errors.New("tls.cert and tls.key must both be set or both empty")
	}
	cert, err := tls.LoadX509KeyPair(cfg.TLS.Cert, cfg.TLS.Key)
	if err != nil {
		return nil, fmt.Errorf("load TLS cert/key: %w", err)
	}
	return httpx.TLSConfig(cert), nil
}

// newLogger builds the root slog.Logger.
//
// format ∈ {"json", "text", ""} (auto: text on a TTY, json otherwise).
// SHADOWNET_LOG_FORMAT in the environment overrides; container images set it
// to json so log aggregators get structured records.
func newLogger(level, format string) *slog.Logger {
	var lvl slog.Level
	if err := lvl.UnmarshalText([]byte(level)); err != nil {
		lvl = slog.LevelInfo
	}
	useJSON := !isTTY(os.Stderr)
	switch format {
	case "json":
		useJSON = true
	case "text":
		useJSON = false
	}
	if useJSON {
		return slog.New(slog.NewJSONHandler(os.Stderr, &slog.HandlerOptions{Level: lvl}))
	}
	return slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: lvl}))
}

func isTTY(f *os.File) bool {
	stat, err := f.Stat()
	if err != nil {
		return false
	}
	return stat.Mode()&os.ModeCharDevice != 0
}
