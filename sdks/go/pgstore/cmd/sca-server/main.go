// SPDX-License-Identifier: MIT

// Command sca-server (pg variant) is the Shadow Certificate Authority HTTP
// server with Postgres support added on top of the default memory + sqlite
// drivers. It is identical in operator-visible surface (flags, YAML, env
// vars, endpoints) to cmd/sca-server in the parent module — the only
// difference is the additional `storage.driver: postgres` option.
package main

import (
	"context"
	"crypto/tls"
	"errors"
	"flag"
	"fmt"
	"log/slog"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"github.com/shadownet-protocol/shadownet/sdks/go/internal/config"
	"github.com/shadownet-protocol/shadownet/sdks/go/pgstore"
	"github.com/shadownet-protocol/shadownet/sdks/go/pkg/crypto"
	"github.com/shadownet-protocol/shadownet/sdks/go/pkg/did"
	"github.com/shadownet-protocol/shadownet/sdks/go/pkg/httpx"
	"github.com/shadownet-protocol/shadownet/sdks/go/pkg/keyguard"
	"github.com/shadownet-protocol/shadownet/sdks/go/pkg/sca"
	"github.com/shadownet-protocol/shadownet/sdks/go/pkg/scaserver"
	"github.com/shadownet-protocol/shadownet/sdks/go/pkg/storemem"
	"github.com/shadownet-protocol/shadownet/sdks/go/pkg/storesqlite"
	"github.com/shadownet-protocol/shadownet/sdks/go/pkg/vc"
)

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
		Driver string `yaml:"driver"` // "memory" | "sqlite" | "postgres"
		DSN    string `yaml:"dsn"`
	} `yaml:"storage"`
	Policy fileConfigPolicy `yaml:"policy"`
}

type fileConfigPolicy struct {
	Levels                 []sca.LevelPolicy `yaml:"levels"`
	FreshnessWindowSeconds int64             `yaml:"freshnessWindowSeconds"`
	StatusListBase         string            `yaml:"statusListBase"`
}

// version is stamped at build time by the release pipeline.
var version = "dev"

// stores is the dependency-injected return shape from openStores. cleanup is
// always non-nil (a no-op for in-memory). readyCheck may be nil for in-memory.
type stores struct {
	sessions   sca.SessionStore
	issuance   sca.IssuanceStore
	revocation sca.RevocationStore
	cleanup    func()
	readyCheck func(context.Context) error
}

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, "sca-server-pg: "+err.Error())
		os.Exit(1)
	}
}

func run() error {
	fs := flag.NewFlagSet("sca-server-pg", flag.ContinueOnError)
	configPath := fs.String("config", "", "path to YAML config file")
	logLevel := fs.String("log-level", config.EnvString("SHADOWNET_LOG_LEVEL", "info"), "log level: debug|info|warn|error")
	showVersion := fs.Bool("version", false, "print version and exit")
	if err := fs.Parse(os.Args[1:]); err != nil {
		return err
	}
	if *showVersion {
		fmt.Printf("sca-server-pg %s\n", version)
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

	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()

	st, err := openStores(ctx, cfg.Storage.Driver, cfg.Storage.DSN)
	if err != nil {
		return err
	}
	defer st.cleanup()

	issuer := &sca.Issuer{
		DID:        cfg.DID,
		KeyID:      keyID,
		Key:        kp,
		Resolver:   buildResolver(cfg.DID),
		Sessions:   st.sessions,
		Issuance:   st.issuance,
		Revocation: st.revocation,
		Methods: map[string]sca.ProofMethod{
			scaserver.InstantApproval: scaserver.InstantApprovalProofMethod{},
		},
		Policy:     policy,
		ReadyCheck: st.readyCheck,
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
		"starting sca-server-pg",
		slog.String("version", version),
		slog.String("did", cfg.DID),
		slog.String("storage", cfg.Storage.Driver),
	)
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
	case "memory", "sqlite", "postgres":
	default:
		return fmt.Errorf("storage.driver must be memory, sqlite, or postgres (got %q)", cfg.Storage.Driver)
	}
	if (cfg.Storage.Driver == "sqlite" || cfg.Storage.Driver == "postgres") && cfg.Storage.DSN == "" {
		return fmt.Errorf("storage.dsn required when driver = %s", cfg.Storage.Driver)
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

func openStores(ctx context.Context, driver, dsn string) (*stores, error) {
	switch driver {
	case "memory":
		return &stores{
			sessions:   storemem.NewSCASessionStore(),
			issuance:   storemem.NewSCAIssuanceStore(),
			revocation: storemem.NewSCARevocationStore(sca.DefaultListID),
			cleanup:    func() {},
		}, nil
	case "sqlite":
		db, err := storesqlite.Open(dsn)
		if err != nil {
			return nil, err
		}
		rev, err := storesqlite.NewSCARevocationStore(db, sca.DefaultListID, 0)
		if err != nil {
			_ = db.Close()
			return nil, err
		}
		return &stores{
			sessions:   storesqlite.NewSCASessionStore(db),
			issuance:   storesqlite.NewSCAIssuanceStore(db),
			revocation: rev,
			cleanup:    func() { _ = db.Close() },
			readyCheck: func(ctx context.Context) error {
				ctx, cancel := context.WithTimeout(ctx, 2*time.Second)
				defer cancel()
				return db.PingContext(ctx)
			},
		}, nil
	case "postgres":
		pool, err := pgstore.Open(ctx, dsn)
		if err != nil {
			return nil, err
		}
		return &stores{
			sessions:   pgstore.NewSCASessionStore(pool),
			issuance:   pgstore.NewSCAIssuanceStore(pool),
			revocation: pgstore.NewSCARevocationStore(pool, sca.DefaultListID, 0),
			cleanup:    pool.Close,
			readyCheck: func(ctx context.Context) error {
				ctx, cancel := context.WithTimeout(ctx, 2*time.Second)
				defer cancel()
				return pool.Ping(ctx)
			},
		}, nil
	default:
		return nil, fmt.Errorf("unknown storage driver %q", driver)
	}
}

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

// newLogger mirrors cmd/sca-server's helper.
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
