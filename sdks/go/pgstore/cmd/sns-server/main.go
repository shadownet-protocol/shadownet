// SPDX-License-Identifier: MIT

// Command sns-server (pg variant) is the Shadow Name Service HTTP server
// with Postgres support added on top of the default memory + sqlite drivers.
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

	"github.com/shadownet-protocol/shadownet-go/internal/config"
	"github.com/shadownet-protocol/shadownet-go/pgstore"
	"github.com/shadownet-protocol/shadownet-go/pkg/crypto"
	"github.com/shadownet-protocol/shadownet-go/pkg/did"
	"github.com/shadownet-protocol/shadownet-go/pkg/httpx"
	"github.com/shadownet-protocol/shadownet-go/pkg/keyguard"
	"github.com/shadownet-protocol/shadownet-go/pkg/sns"
	"github.com/shadownet-protocol/shadownet-go/pkg/snsserver"
	"github.com/shadownet-protocol/shadownet-go/pkg/storemem"
	"github.com/shadownet-protocol/shadownet-go/pkg/storesqlite"
)

type fileConfig struct {
	DID      string `yaml:"did"`
	Provider string `yaml:"provider"`
	Listen   string `yaml:"listen"`
	TLS      struct {
		Cert string `yaml:"cert"`
		Key  string `yaml:"key"`
	} `yaml:"tls"`
	Signing struct {
		KeyFile string `yaml:"keyfile"`
	} `yaml:"signing"`
	Storage struct {
		Driver string `yaml:"driver"` // memory | sqlite | postgres
		DSN    string `yaml:"dsn"`
	} `yaml:"storage"`
	DefaultTTL int `yaml:"defaultTtl"`
}

var version = "dev"

type recordStore struct {
	store      sns.RecordStore
	cleanup    func()
	readyCheck func(context.Context) error
}

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, "sns-server-pg: "+err.Error())
		os.Exit(1)
	}
}

func run() error {
	fs := flag.NewFlagSet("sns-server-pg", flag.ContinueOnError)
	configPath := fs.String("config", "", "path to YAML config file")
	logLevel := fs.String("log-level", config.EnvString("SHADOWNET_LOG_LEVEL", "info"), "log level: debug|info|warn|error")
	showVersion := fs.Bool("version", false, "print version and exit")
	if err := fs.Parse(os.Args[1:]); err != nil {
		return err
	}
	if *showVersion {
		fmt.Printf("sns-server-pg %s\n", version)
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
	if err := keyguard.AssertNotFixture(kp.Public, "sns-server"); err != nil {
		return err
	}
	keyID := cfg.DID + "#sns-1"

	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()

	rs, err := openStore(ctx, cfg.Storage.Driver, cfg.Storage.DSN)
	if err != nil {
		return err
	}
	defer rs.cleanup()

	server := &sns.Server{
		ProviderDID: cfg.DID,
		ProviderKID: keyID,
		Provider:    cfg.Provider,
		Key:         kp,
		Records:     rs.store,
		DIDResolver: buildResolver(cfg.DID),
		DefaultTTL:  cfg.DefaultTTL,
		ReadyCheck:  rs.readyCheck,
		Logger:      logger,
	}
	if err := server.Validate(); err != nil {
		return err
	}

	tlsCfg, err := buildTLS(cfg)
	if err != nil {
		return err
	}

	logger.Info(
		"starting sns-server-pg",
		slog.String("version", version),
		slog.String("did", cfg.DID),
		slog.String("provider", cfg.Provider),
		slog.String("storage", cfg.Storage.Driver),
	)
	return snsserver.Run(ctx, snsserver.RunConfig{
		Server: server,
		Listen: cfg.Listen,
		TLS:    tlsCfg,
		Logger: logger,
	})
}

func validateConfig(cfg *fileConfig) error {
	if cfg.DID == "" || cfg.Provider == "" || cfg.Listen == "" || cfg.Signing.KeyFile == "" {
		return errors.New("did, provider, listen, signing.keyfile are required")
	}
	if cfg.Storage.Driver == "" {
		cfg.Storage.Driver = "memory"
	}
	switch cfg.Storage.Driver {
	case "memory", "sqlite", "postgres":
	default:
		return fmt.Errorf("storage.driver must be memory, sqlite, or postgres (got %q)", cfg.Storage.Driver)
	}
	if cfg.DefaultTTL == 0 {
		cfg.DefaultTTL = 300
	}
	return nil
}

func applyEnvOverrides(cfg *fileConfig) {
	cfg.DID = config.EnvString("SHADOWNET_DID", cfg.DID)
	cfg.Provider = config.EnvString("SHADOWNET_PROVIDER", cfg.Provider)
	cfg.Listen = config.EnvString("SHADOWNET_LISTEN", cfg.Listen)
	cfg.TLS.Cert = config.EnvString("SHADOWNET_TLS_CERT", cfg.TLS.Cert)
	cfg.TLS.Key = config.EnvString("SHADOWNET_TLS_KEY", cfg.TLS.Key)
	cfg.Signing.KeyFile = config.EnvString("SHADOWNET_SIGNING_KEYFILE", cfg.Signing.KeyFile)
	cfg.Storage.Driver = config.EnvString("SHADOWNET_STORAGE_DRIVER", cfg.Storage.Driver)
	cfg.Storage.DSN = config.EnvString("SHADOWNET_STORAGE_DSN", cfg.Storage.DSN)
}

func openStore(ctx context.Context, driver, dsn string) (*recordStore, error) {
	switch driver {
	case "memory":
		return &recordStore{store: storemem.NewSNSRecordStore(), cleanup: func() {}}, nil
	case "sqlite":
		if dsn == "" {
			return nil, errors.New("storage.dsn required when driver = sqlite")
		}
		db, err := storesqlite.OpenSNS(dsn)
		if err != nil {
			return nil, err
		}
		return &recordStore{
			store:   storesqlite.NewSNSRecordStore(db),
			cleanup: func() { _ = db.Close() },
			readyCheck: func(ctx context.Context) error {
				ctx, cancel := context.WithTimeout(ctx, 2*time.Second)
				defer cancel()
				return db.PingContext(ctx)
			},
		}, nil
	case "postgres":
		if dsn == "" {
			return nil, errors.New("storage.dsn required when driver = postgres")
		}
		pool, err := pgstore.Open(ctx, dsn)
		if err != nil {
			return nil, err
		}
		return &recordStore{
			store:   pgstore.NewSNSRecordStore(pool),
			cleanup: pool.Close,
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

func buildResolver(snsDID string) did.Resolver {
	if strings.HasPrefix(snsDID, "did:web:") {
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
