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
	"errors"
	"flag"
	"fmt"
	"log/slog"
	"net"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"github.com/shadownet-protocol/shadownet-go/internal/config"
	"github.com/shadownet-protocol/shadownet-go/internal/httpx"
	"github.com/shadownet-protocol/shadownet-go/internal/storemem"
	"github.com/shadownet-protocol/shadownet-go/pkg/crypto"
	"github.com/shadownet-protocol/shadownet-go/pkg/did"
	"github.com/shadownet-protocol/shadownet-go/pkg/sca"
	"github.com/shadownet-protocol/shadownet-go/pkg/vc"
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
	if err := fs.Parse(os.Args[1:]); err != nil {
		return err
	}
	if *configPath == "" {
		return errors.New("--config is required")
	}

	logger := newLogger(*logLevel)
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
	keyID := cfg.DID + "#" + sca.DefaultListID // simple key fragment; matches did:web doc we serve

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

	sessions, issuance, revocation, err := openStores(cfg.Storage.Driver, cfg.Storage.DSN)
	if err != nil {
		return err
	}

	resolver, err := buildResolver(cfg.DID)
	if err != nil {
		return err
	}

	issuer := &sca.Issuer{
		DID:        cfg.DID,
		KeyID:      keyID,
		Key:        kp,
		Resolver:   resolver,
		Sessions:   sessions,
		Issuance:   issuance,
		Revocation: revocation,
		Methods: map[string]sca.ProofMethod{
			InstantApproval: InstantApprovalProofMethod{},
		},
		Policy: policy,
	}
	if err := issuer.Validate(); err != nil {
		return err
	}

	tlsCfg, err := buildTLS(cfg, cfg.Listen)
	if err != nil {
		return err
	}

	srv := httpx.NewServer(issuer.Handler(), httpx.ServerOptions{
		Addr:      cfg.Listen,
		TLSConfig: tlsCfg,
		Logger:    logger,
	})

	if tlsCfg == nil {
		warnIfNotLoopback(logger, cfg.Listen)
	}

	logger.Info("starting sca-server",
		slog.String("did", cfg.DID),
		slog.String("listen", cfg.Listen),
		slog.Bool("tls", tlsCfg != nil),
		slog.String("storage", cfg.Storage.Driver),
	)

	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	return httpx.ListenAndServe(ctx, srv)
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

func openStores(driver, dsn string) (sca.SessionStore, sca.IssuanceStore, sca.RevocationStore, error) {
	switch driver {
	case "memory":
		return storemem.NewSCASessionStore(),
			storemem.NewSCAIssuanceStore(),
			storemem.NewSCARevocationStore(sca.DefaultListID),
			nil
	case "sqlite":
		return openSQLiteStores(dsn)
	default:
		return nil, nil, nil, fmt.Errorf("unknown storage driver %q", driver)
	}
}

// buildResolver returns a DID resolver. If the SCA's DID is did:web, we
// configure a WebResolver; for did:key we return the local resolver and
// dispatcher only.
func buildResolver(scaDID string) (did.Resolver, error) {
	if strings.HasPrefix(scaDID, "did:web:") {
		return did.NewResolver(did.NewWebResolver()), nil
	}
	return did.NewResolver(nil), nil
}

func buildTLS(cfg fileConfig, listen string) (*tls.Config, error) {
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
	_ = listen
	return httpx.TLSConfig(cert), nil
}

func warnIfNotLoopback(logger *slog.Logger, addr string) {
	host, _, err := net.SplitHostPort(addr)
	if err != nil {
		return
	}
	if host == "" || host == "0.0.0.0" || host == "::" {
		logger.Warn("plaintext HTTP on a non-loopback address; configure tls.cert and tls.key for production", slog.String("listen", addr))
		return
	}
	if ip := net.ParseIP(host); ip != nil && !ip.IsLoopback() {
		logger.Warn("plaintext HTTP on a non-loopback address; configure tls.cert and tls.key for production", slog.String("listen", addr))
	}
}

func newLogger(level string) *slog.Logger {
	var lvl slog.Level
	if err := lvl.UnmarshalText([]byte(level)); err != nil {
		lvl = slog.LevelInfo
	}
	if isTTY(os.Stderr) {
		return slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: lvl}))
	}
	return slog.New(slog.NewJSONHandler(os.Stderr, &slog.HandlerOptions{Level: lvl}))
}

func isTTY(f *os.File) bool {
	stat, err := f.Stat()
	if err != nil {
		return false
	}
	return stat.Mode()&os.ModeCharDevice != 0
}
