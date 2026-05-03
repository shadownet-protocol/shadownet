// SPDX-License-Identifier: MIT

// Command sns-server is the reference Shadow Name Service HTTP server. It
// implements the RFC-0005 endpoints atop pkg/sns.
package main

import (
	"context"
	"crypto/tls"
	"database/sql"
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
	"github.com/shadownet-protocol/shadownet-go/internal/storesqlite"
	"github.com/shadownet-protocol/shadownet-go/pkg/crypto"
	"github.com/shadownet-protocol/shadownet-go/pkg/did"
	"github.com/shadownet-protocol/shadownet-go/pkg/sns"
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
		Driver string `yaml:"driver"` // memory | sqlite
		DSN    string `yaml:"dsn"`
	} `yaml:"storage"`
	DefaultTTL int `yaml:"defaultTtl"`
}

// version is stamped at build time by the release pipeline via
// `-ldflags "-X main.version=$tag"`. Local builds keep the "dev" sentinel.
var version = "dev"

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, "sns-server: "+err.Error())
		os.Exit(1)
	}
}

func run() error {
	fs := flag.NewFlagSet("sns-server", flag.ContinueOnError)
	configPath := fs.String("config", "", "path to YAML config file")
	logLevel := fs.String("log-level", config.EnvString("SHADOWNET_LOG_LEVEL", "info"), "log level: debug|info|warn|error")
	showVersion := fs.Bool("version", false, "print version and exit")
	if err := fs.Parse(os.Args[1:]); err != nil {
		return err
	}
	if *showVersion {
		fmt.Printf("sns-server %s\n", version)
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
	cfg.DID = config.EnvString("SHADOWNET_DID", cfg.DID)
	cfg.Provider = config.EnvString("SHADOWNET_PROVIDER", cfg.Provider)
	cfg.Listen = config.EnvString("SHADOWNET_LISTEN", cfg.Listen)
	cfg.TLS.Cert = config.EnvString("SHADOWNET_TLS_CERT", cfg.TLS.Cert)
	cfg.TLS.Key = config.EnvString("SHADOWNET_TLS_KEY", cfg.TLS.Key)
	cfg.Signing.KeyFile = config.EnvString("SHADOWNET_SIGNING_KEYFILE", cfg.Signing.KeyFile)
	cfg.Storage.Driver = config.EnvString("SHADOWNET_STORAGE_DRIVER", cfg.Storage.Driver)
	cfg.Storage.DSN = config.EnvString("SHADOWNET_STORAGE_DSN", cfg.Storage.DSN)
	if cfg.Storage.Driver == "" {
		cfg.Storage.Driver = "memory"
	}
	if cfg.DefaultTTL == 0 {
		cfg.DefaultTTL = 300
	}

	if cfg.DID == "" || cfg.Provider == "" || cfg.Listen == "" || cfg.Signing.KeyFile == "" {
		return errors.New("did, provider, listen, signing.keyfile are required")
	}

	kp, err := crypto.LoadKeyFile(cfg.Signing.KeyFile)
	if err != nil {
		return fmt.Errorf("load signing key: %w (generate one with `shadownet keygen`)", err)
	}
	keyID := cfg.DID + "#sns-1"

	resolver := buildResolver(cfg.DID)

	store, db, err := openStore(cfg.Storage.Driver, cfg.Storage.DSN)
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

	server := &sns.Server{
		ProviderDID: cfg.DID,
		ProviderKID: keyID,
		Provider:    cfg.Provider,
		Key:         kp,
		Records:     store,
		DIDResolver: resolver,
		DefaultTTL:  cfg.DefaultTTL,
		ReadyCheck:  readyCheck(db),
	}
	if err := server.Validate(); err != nil {
		return err
	}

	tlsCfg, err := buildTLS(cfg)
	if err != nil {
		return err
	}
	srv := httpx.NewServer(server.Handler(), httpx.ServerOptions{
		Addr: cfg.Listen, TLSConfig: tlsCfg, Logger: logger,
	})
	if tlsCfg == nil {
		warnIfNotLoopback(logger, cfg.Listen)
	}

	logger.Info("starting sns-server",
		slog.String("version", version),
		slog.String("did", cfg.DID), slog.String("provider", cfg.Provider),
		slog.String("listen", cfg.Listen), slog.Bool("tls", tlsCfg != nil),
		slog.String("storage", cfg.Storage.Driver),
	)

	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	return httpx.ListenAndServe(ctx, srv)
}

// openStore returns the record store plus an optional *sql.DB to close on
// shutdown (nil for the in-memory driver).
func openStore(driver, dsn string) (sns.RecordStore, *sql.DB, error) {
	switch driver {
	case "memory":
		return storemem.NewSNSRecordStore(), nil, nil
	case "sqlite":
		if dsn == "" {
			return nil, nil, errors.New("storage.dsn required when driver = sqlite")
		}
		db, err := storesqlite.OpenSNS(dsn)
		if err != nil {
			return nil, nil, err
		}
		return storesqlite.NewSNSRecordStore(db), db, nil
	default:
		return nil, nil, fmt.Errorf("unknown storage driver %q", driver)
	}
}

// readyCheck returns a /readyz hook that pings db, or nil for the memory
// driver where there is nothing external to verify.
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

// newLogger builds the root slog.Logger. format ∈ {"json","text",""};
// SHADOWNET_LOG_FORMAT overrides the auto-by-TTY default.
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
