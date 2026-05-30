// SPDX-License-Identifier: MIT

// Command issuer-server is the Shadownet Issuer HTTP reference server
// (RFC 0001 §6). It supports both domain mode (well-known paths) and
// keyed-Hub mode (self-served AgentCard + configurable paths).
//
// Subcommands:
//
//	issuer-server serve  --config issuer.yaml
//	issuer-server admin approve     --config issuer.yaml --handle <hex>
//	issuer-server admin reject      --config issuer.yaml --handle <hex> [--reason "..."]
//	issuer-server admin revoke      --config issuer.yaml --epoch <n> --idx <n>
//	issuer-server admin rotate-epoch --config issuer.yaml
//	issuer-server admin list-pending --config issuer.yaml [--status new|approved|rejected]
//
// Config file (YAML):
//
//	listen: 127.0.0.1:8444
//	mode: domain                  # or "keyed"
//	issuerIdentifier: acme.example  # domain or z6Mk… pubkey
//	cacheMaxAge: 300              # status-list Cache-Control
//	signing:
//	  keyfile: ./issuer.jwk
//	storage:
//	  driver: sqlite
//	  dsn: ./issuer.db
//	  maxIndicesPerEpoch: 131072
//	tls:
//	  cert: ./tls.crt             # optional; omit for plaintext (loopback only)
//	  key:  ./tls.key
//	hook:
//	  driver: queue               # queue | dev | webhook (webhook deferred)
//	  nextURL: https://acme.example/.well-known/shadownet/issue
//	# Required only when mode == keyed:
//	keyedAgentCard:
//	  name: "Acme Hub"
//	  description: "Membership credentials for acme.example"
//	  a2aURL:         https://hub.acme.example/a2a
//	  issueURL:       https://hub.acme.example/issue
//	  statusListBase: https://hub.acme.example/status
package main

import (
	"context"
	"crypto/tls"
	"errors"
	"flag"
	"fmt"
	"io"
	"log/slog"
	"os"
	"os/signal"
	"strconv"
	"syscall"
	"time"

	"github.com/shadownet-protocol/shadownet/core/internal/config"
	"github.com/shadownet-protocol/shadownet/core/internal/crypto"
	"github.com/shadownet-protocol/shadownet/core/internal/identifiers"
	"github.com/shadownet-protocol/shadownet/core/internal/issuer"
	"github.com/shadownet-protocol/shadownet/core/internal/issuer/hooks/dev"
	"github.com/shadownet-protocol/shadownet/core/internal/issuer/hooks/queue"
	"github.com/shadownet-protocol/shadownet/core/internal/issuer/sqlitestore"
	"github.com/shadownet-protocol/shadownet/core/internal/keyguard"
)

type fileConfig struct {
	Listen           string        `yaml:"listen"`
	Mode             string        `yaml:"mode"`
	IssuerIdentifier string        `yaml:"issuerIdentifier"`
	CacheMaxAge      int           `yaml:"cacheMaxAge"`
	Signing          signingBlock  `yaml:"signing"`
	Storage          storageBlock  `yaml:"storage"`
	TLS              tlsBlock      `yaml:"tls"`
	Hook             hookBlock     `yaml:"hook"`
	KeyedAgentCard   keyedBlock    `yaml:"keyedAgentCard"`
	ShutdownTimeout  time.Duration `yaml:"shutdownTimeout"`
}

type signingBlock struct {
	Keyfile string `yaml:"keyfile"`
}

type storageBlock struct {
	Driver             string `yaml:"driver"`
	DSN                string `yaml:"dsn"`
	MaxIndicesPerEpoch uint64 `yaml:"maxIndicesPerEpoch"`
}

type tlsBlock struct {
	Cert string `yaml:"cert"`
	Key  string `yaml:"key"`
}

type hookBlock struct {
	Driver  string `yaml:"driver"`
	NextURL string `yaml:"nextURL"`
}

type keyedBlock struct {
	Name           string `yaml:"name"`
	Description    string `yaml:"description"`
	Version        string `yaml:"version"`
	A2AURL         string `yaml:"a2aURL"`
	IssueURL       string `yaml:"issueURL"`
	StatusListBase string `yaml:"statusListBase"`
}

func main() {
	if len(os.Args) < 2 {
		usage(os.Stderr)
		os.Exit(2)
	}
	cmd := os.Args[1]
	args := os.Args[2:]
	var err error
	switch cmd {
	case "serve":
		err = runServe(args)
	case "admin":
		if len(args) == 0 {
			err = errors.New("issuer-server: admin <subcommand> required")
		} else {
			err = runAdmin(args[0], args[1:], os.Stdout)
		}
	case "version", "--version", "-v":
		fmt.Fprintln(os.Stdout, "issuer-server (dev) (protocol v0.2)")
	case "-h", "--help":
		usage(os.Stdout)
	default:
		usage(os.Stderr)
		os.Exit(2)
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "issuer-server:", err)
		os.Exit(1)
	}
}

func usage(w io.Writer) {
	fmt.Fprintln(w, `issuer-server — Shadownet Issuer HTTP reference (RFC 0001 §6)

Usage:
  issuer-server serve  --config <issuer.yaml>
  issuer-server admin approve      --config <yaml> --handle <hex>
  issuer-server admin reject       --config <yaml> --handle <hex> [--reason "..."]
  issuer-server admin revoke       --config <yaml> --epoch <n> --idx <n>
  issuer-server admin rotate-epoch --config <yaml>
  issuer-server admin list-pending --config <yaml> [--status new|approved|rejected]
  issuer-server version`)
}

func loadFileConfig(path string) (fileConfig, error) {
	if path == "" {
		return fileConfig{}, errors.New("--config required")
	}
	var c fileConfig
	if err := config.Load(path, &c); err != nil {
		return fileConfig{}, err
	}
	if c.Listen == "" {
		return fileConfig{}, errors.New("config.listen required")
	}
	if c.IssuerIdentifier == "" {
		return fileConfig{}, errors.New("config.issuerIdentifier required")
	}
	if c.Signing.Keyfile == "" {
		return fileConfig{}, errors.New("config.signing.keyfile required")
	}
	if c.Storage.Driver == "" {
		c.Storage.Driver = "sqlite"
	}
	if c.Storage.Driver != "sqlite" {
		return fileConfig{}, fmt.Errorf("config.storage.driver %q not supported in this build", c.Storage.Driver)
	}
	if c.Storage.DSN == "" {
		return fileConfig{}, errors.New("config.storage.dsn required")
	}
	if c.Mode == "" {
		c.Mode = "domain"
	}
	if c.Mode != "domain" && c.Mode != "keyed" {
		return fileConfig{}, fmt.Errorf("config.mode %q must be domain or keyed", c.Mode)
	}
	if c.Hook.Driver == "" {
		c.Hook.Driver = "queue"
	}
	return c, nil
}

func loadSigner(path string) (crypto.KeyPair, error) {
	kp, err := crypto.LoadKeyFile(path)
	if err != nil {
		return crypto.KeyPair{}, fmt.Errorf("load signing key: %w", err)
	}
	if err := keyguard.AssertNotFixture(kp.Public, "issuer"); err != nil {
		return crypto.KeyPair{}, err
	}
	return kp, nil
}

func parseMode(s string) issuer.Mode {
	if s == "keyed" {
		return issuer.ModeKeyed
	}
	return issuer.ModeDomain
}

func runServe(args []string) error {
	fs := flag.NewFlagSet("serve", flag.ContinueOnError)
	cfgPath := fs.String("config", "", "path to issuer.yaml")
	if err := fs.Parse(args); err != nil {
		return err
	}
	cfg, err := loadFileConfig(*cfgPath)
	if err != nil {
		return err
	}
	kp, err := loadSigner(cfg.Signing.Keyfile)
	if err != nil {
		return err
	}
	store, err := sqlitestore.Open(cfg.Storage.DSN, cfg.Storage.MaxIndicesPerEpoch)
	if err != nil {
		return err
	}
	defer func() { _ = store.Close() }()

	logger := slog.New(slog.NewJSONHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelInfo}))

	mode := parseMode(cfg.Mode)
	if mode == issuer.ModeDomain {
		if identifiers.Classify(cfg.IssuerIdentifier) != identifiers.ClassDomain {
			return fmt.Errorf("domain mode requires a domain issuerIdentifier; got %q", cfg.IssuerIdentifier)
		}
	} else {
		if identifiers.Classify(cfg.IssuerIdentifier) != identifiers.ClassPubKey {
			return fmt.Errorf("keyed mode requires a multibase pubkey issuerIdentifier; got %q", cfg.IssuerIdentifier)
		}
	}

	hook, err := buildHook(cfg.Hook, store, logger, cfg.Listen)
	if err != nil {
		return err
	}

	authz := issuer.NewAuthorizer(issuer.AuthzConfig{})

	hConfig := issuer.HandlerConfig{
		Mode:               mode,
		Store:              store,
		Hook:               hook,
		Authz:              authz,
		Signer:             kp.Private,
		Logger:             logger,
		IssuerIdentifier:   cfg.IssuerIdentifier,
		StatusCacheSeconds: cfg.CacheMaxAge,
	}
	if mode == issuer.ModeKeyed {
		hConfig.KeyedAgentCardSubject = issuer.KeyedAgentCardConfig{
			Name:           cfg.KeyedAgentCard.Name,
			Description:    cfg.KeyedAgentCard.Description,
			Version:        cfg.KeyedAgentCard.Version,
			A2AURL:         cfg.KeyedAgentCard.A2AURL,
			IssueURL:       cfg.KeyedAgentCard.IssueURL,
			StatusListBase: cfg.KeyedAgentCard.StatusListBase,
		}
	}

	var tlsConfig *tls.Config
	if cfg.TLS.Cert != "" || cfg.TLS.Key != "" {
		cert, err := tls.LoadX509KeyPair(cfg.TLS.Cert, cfg.TLS.Key)
		if err != nil {
			return fmt.Errorf("load TLS keypair: %w", err)
		}
		tlsConfig = &tls.Config{Certificates: []tls.Certificate{cert}, MinVersion: tls.VersionTLS13}
	}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	logger.Info(
		"issuer-server starting",
		"listen", cfg.Listen,
		"mode", mode.String(),
		"issuer", cfg.IssuerIdentifier,
		"hook", cfg.Hook.Driver,
		"tls", tlsConfig != nil,
	)
	return issuer.Run(ctx, issuer.RunConfig{
		HandlerConfig:   hConfig,
		ListenAddr:      cfg.Listen,
		ShutdownTimeout: cfg.ShutdownTimeout,
		TLSConfig:       tlsConfig,
	})
}

func buildHook(h hookBlock, store issuer.Store, logger *slog.Logger, listenAddr string) (issuer.Hook, error) {
	switch h.Driver {
	case "dev":
		if err := dev.AssertAutoApproveNotPublic(logger, listenAddr); err != nil {
			return nil, err
		}
		return dev.NewAutoApproveHook(), nil
	case "queue":
		if h.NextURL == "" {
			return nil, errors.New("config.hook.nextURL required for queue hook")
		}
		return queue.New(queue.Config{Store: store, NextURL: h.NextURL})
	default:
		return nil, fmt.Errorf("config.hook.driver %q not supported", h.Driver)
	}
}

func runAdmin(sub string, args []string, out io.Writer) error {
	switch sub {
	case "approve":
		return runAdminApprove(args, out)
	case "reject":
		return runAdminReject(args, out)
	case "revoke":
		return runAdminRevoke(args, out)
	case "rotate-epoch":
		return runAdminRotate(args, out)
	case "list-pending":
		return runAdminListPending(args, out)
	default:
		return fmt.Errorf("unknown admin subcommand %q", sub)
	}
}

func openStore(cfgPath string) (*sqlitestore.Store, error) {
	cfg, err := loadFileConfig(cfgPath)
	if err != nil {
		return nil, err
	}
	return sqlitestore.Open(cfg.Storage.DSN, cfg.Storage.MaxIndicesPerEpoch)
}

func runAdminApprove(args []string, out io.Writer) error {
	fs := flag.NewFlagSet("admin approve", flag.ContinueOnError)
	cfgPath := fs.String("config", "", "path to issuer.yaml")
	handle := fs.String("handle", "", "pending ceremony handle")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if *handle == "" {
		return errors.New("--handle required")
	}
	store, err := openStore(*cfgPath)
	if err != nil {
		return err
	}
	defer func() { _ = store.Close() }()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := store.UpdatePendingStatus(ctx, *handle, issuer.PendingApproved, "", time.Now()); err != nil {
		return err
	}
	fmt.Fprintln(out, "approved", *handle)
	return nil
}

func runAdminReject(args []string, out io.Writer) error {
	fs := flag.NewFlagSet("admin reject", flag.ContinueOnError)
	cfgPath := fs.String("config", "", "path to issuer.yaml")
	handle := fs.String("handle", "", "pending ceremony handle")
	reason := fs.String("reason", "", "rejection reason surfaced to the Subject")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if *handle == "" {
		return errors.New("--handle required")
	}
	store, err := openStore(*cfgPath)
	if err != nil {
		return err
	}
	defer func() { _ = store.Close() }()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := store.UpdatePendingStatus(ctx, *handle, issuer.PendingRejected, *reason, time.Now()); err != nil {
		return err
	}
	fmt.Fprintln(out, "rejected", *handle)
	return nil
}

func runAdminRevoke(args []string, out io.Writer) error {
	fs := flag.NewFlagSet("admin revoke", flag.ContinueOnError)
	cfgPath := fs.String("config", "", "path to issuer.yaml")
	epoch := fs.Uint64("epoch", 0, "epoch number")
	idx := fs.Uint64("idx", 0, "revocation index")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if *epoch == 0 {
		return errors.New("--epoch required")
	}
	store, err := openStore(*cfgPath)
	if err != nil {
		return err
	}
	defer func() { _ = store.Close() }()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := store.SetRevoked(ctx, *epoch, *idx, time.Now()); err != nil {
		return err
	}
	fmt.Fprintln(out, "revoked epoch", *epoch, "idx", *idx)
	return nil
}

func runAdminRotate(args []string, out io.Writer) error {
	fs := flag.NewFlagSet("admin rotate-epoch", flag.ContinueOnError)
	cfgPath := fs.String("config", "", "path to issuer.yaml")
	maxIndices := fs.Uint64("max-indices", 0, "ceiling for the new epoch (0 → store default)")
	if err := fs.Parse(args); err != nil {
		return err
	}
	store, err := openStore(*cfgPath)
	if err != nil {
		return err
	}
	defer func() { _ = store.Close() }()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	e, err := store.RotateEpoch(ctx, *maxIndices, time.Now())
	if err != nil {
		return err
	}
	fmt.Fprintln(out, "opened epoch", e.Number, "max_indices", e.MaxIndices)
	return nil
}

func runAdminListPending(args []string, out io.Writer) error {
	fs := flag.NewFlagSet("admin list-pending", flag.ContinueOnError)
	cfgPath := fs.String("config", "", "path to issuer.yaml")
	statusFlag := fs.String("status", "", "filter by status (new|approved|rejected)")
	limit := fs.Int("limit", 0, "max rows to print")
	if err := fs.Parse(args); err != nil {
		return err
	}
	store, err := openStore(*cfgPath)
	if err != nil {
		return err
	}
	defer func() { _ = store.Close() }()

	filter := issuer.PendingFilter{Limit: *limit}
	if *statusFlag != "" {
		var s issuer.PendingStatus
		switch *statusFlag {
		case "new":
			s = issuer.PendingNew
		case "approved":
			s = issuer.PendingApproved
		case "rejected":
			s = issuer.PendingRejected
		default:
			return fmt.Errorf("unknown --status %q", *statusFlag)
		}
		filter.Status = &s
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	rows, err := store.ListPending(ctx, filter)
	if err != nil {
		return err
	}
	if len(rows) == 0 {
		fmt.Fprintln(out, "(no pending ceremonies)")
		return nil
	}
	for _, p := range rows {
		fmt.Fprintf(
			out, "%s\t%s\t%s\t%s\t%s\texp=%s\n",
			p.HandleID, p.Status, p.Iss, p.Aud, p.Org,
			strconv.FormatInt(p.CeremonyExpiry.Unix(), 10),
		)
	}
	return nil
}
