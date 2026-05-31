// SPDX-License-Identifier: MIT

// Package cmdrun is the shared command-line surface for the Shadownet
// Issuer reference servers. Both the SQLite-backed cmd/issuer-server in
// the main core module and the Postgres-backed cmd/issuer-server-pg in
// the pgstore submodule wire their store implementation in through
// Options and call Main.
//
// All flag parsing, YAML config loading, signing-key loading, mode
// dispatch (domain ∣ keyed), hook construction, signal handling, and
// admin subcommands live here so the two cmd binaries stay thin shims.
package cmdrun

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
	"github.com/shadownet-protocol/shadownet/core/internal/keyguard"
)

// Options is the per-binary wiring. Callers supply the storage driver
// they accept and a constructor that opens it from the parsed YAML
// storage block.
type Options struct {
	// BinaryName is the program name used in error messages and usage
	// output ("issuer-server" or "issuer-server-pg").
	BinaryName string
	// StorageDriver is the only `storage.driver` value the binary will
	// accept ("sqlite" or "postgres"). Configs that name a different
	// driver are rejected with a clear error.
	StorageDriver string
	// OpenStore opens the store from the YAML config's storage block.
	// The returned issuer.Store has the same Close lifecycle as the cmd
	// binary itself; cmdrun calls Close on exit.
	OpenStore func(ctx context.Context, dsn string, maxIndices uint64) (issuer.Store, error)
}

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

// Main parses args, dispatches the subcommand, and returns an error
// suitable for the caller to print + exit on. A nil return means success.
func Main(args []string, opts Options) error {
	if opts.BinaryName == "" || opts.StorageDriver == "" || opts.OpenStore == nil {
		return errors.New("cmdrun: Options.BinaryName, StorageDriver, OpenStore all required")
	}
	if len(args) == 0 {
		usage(os.Stderr, opts.BinaryName)
		return errors.New("missing subcommand")
	}
	cmd, sub := args[0], args[1:]
	switch cmd {
	case "serve":
		return runServe(sub, opts)
	case "admin":
		if len(sub) == 0 {
			return fmt.Errorf("%s: admin <subcommand> required", opts.BinaryName)
		}
		return runAdmin(sub[0], sub[1:], opts, os.Stdout)
	case "version", "--version", "-v":
		fmt.Fprintf(os.Stdout, "%s (dev) (protocol v0.2)\n", opts.BinaryName)
		return nil
	case "-h", "--help":
		usage(os.Stdout, opts.BinaryName)
		return nil
	default:
		usage(os.Stderr, opts.BinaryName)
		return fmt.Errorf("unknown subcommand %q", cmd)
	}
}

func usage(w io.Writer, name string) {
	fmt.Fprintf(w, `%s — Shadownet Issuer HTTP reference (RFC 0001 §6)

Usage:
  %[1]s serve  --config <issuer.yaml>
  %[1]s admin approve      --config <yaml> --handle <hex>
  %[1]s admin reject       --config <yaml> --handle <hex> [--reason "..."]
  %[1]s admin revoke       --config <yaml> --epoch <n> --idx <n>
  %[1]s admin rotate-epoch --config <yaml>
  %[1]s admin list-pending --config <yaml> [--status new|approved|rejected]
  %[1]s version
`, name)
}

func loadFileConfig(path string, opts Options) (fileConfig, error) {
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
		c.Storage.Driver = opts.StorageDriver
	}
	if c.Storage.Driver != opts.StorageDriver {
		return fileConfig{}, fmt.Errorf("config.storage.driver %q not supported in this build (%s only)", c.Storage.Driver, opts.StorageDriver)
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

func runServe(args []string, opts Options) error {
	fs := flag.NewFlagSet("serve", flag.ContinueOnError)
	cfgPath := fs.String("config", "", "path to issuer.yaml")
	if err := fs.Parse(args); err != nil {
		return err
	}
	cfg, err := loadFileConfig(*cfgPath, opts)
	if err != nil {
		return err
	}
	kp, err := loadSigner(cfg.Signing.Keyfile)
	if err != nil {
		return err
	}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	store, err := opts.OpenStore(ctx, cfg.Storage.DSN, cfg.Storage.MaxIndicesPerEpoch)
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

	logger.Info(
		opts.BinaryName+" starting",
		"listen", cfg.Listen,
		"mode", mode.String(),
		"issuer", cfg.IssuerIdentifier,
		"hook", cfg.Hook.Driver,
		"storage", opts.StorageDriver,
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

func runAdmin(sub string, args []string, opts Options, out io.Writer) error {
	switch sub {
	case "approve":
		return runAdminApprove(args, opts, out)
	case "reject":
		return runAdminReject(args, opts, out)
	case "revoke":
		return runAdminRevoke(args, opts, out)
	case "rotate-epoch":
		return runAdminRotate(args, opts, out)
	case "list-pending":
		return runAdminListPending(args, opts, out)
	default:
		return fmt.Errorf("unknown admin subcommand %q", sub)
	}
}

func openAdminStore(ctx context.Context, cfgPath string, opts Options) (issuer.Store, error) {
	cfg, err := loadFileConfig(cfgPath, opts)
	if err != nil {
		return nil, err
	}
	return opts.OpenStore(ctx, cfg.Storage.DSN, cfg.Storage.MaxIndicesPerEpoch)
}

func runAdminApprove(args []string, opts Options, out io.Writer) error {
	fs := flag.NewFlagSet("admin approve", flag.ContinueOnError)
	cfgPath := fs.String("config", "", "path to issuer.yaml")
	handle := fs.String("handle", "", "pending ceremony handle")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if *handle == "" {
		return errors.New("--handle required")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	store, err := openAdminStore(ctx, *cfgPath, opts)
	if err != nil {
		return err
	}
	defer func() { _ = store.Close() }()
	if err := store.UpdatePendingStatus(ctx, *handle, issuer.PendingApproved, "", time.Now()); err != nil {
		return err
	}
	fmt.Fprintln(out, "approved", *handle)
	return nil
}

func runAdminReject(args []string, opts Options, out io.Writer) error {
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
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	store, err := openAdminStore(ctx, *cfgPath, opts)
	if err != nil {
		return err
	}
	defer func() { _ = store.Close() }()
	if err := store.UpdatePendingStatus(ctx, *handle, issuer.PendingRejected, *reason, time.Now()); err != nil {
		return err
	}
	fmt.Fprintln(out, "rejected", *handle)
	return nil
}

func runAdminRevoke(args []string, opts Options, out io.Writer) error {
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
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	store, err := openAdminStore(ctx, *cfgPath, opts)
	if err != nil {
		return err
	}
	defer func() { _ = store.Close() }()
	if err := store.SetRevoked(ctx, *epoch, *idx, time.Now()); err != nil {
		return err
	}
	fmt.Fprintln(out, "revoked epoch", *epoch, "idx", *idx)
	return nil
}

func runAdminRotate(args []string, opts Options, out io.Writer) error {
	fs := flag.NewFlagSet("admin rotate-epoch", flag.ContinueOnError)
	cfgPath := fs.String("config", "", "path to issuer.yaml")
	maxIndices := fs.Uint64("max-indices", 0, "ceiling for the new epoch (0 → store default)")
	if err := fs.Parse(args); err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	store, err := openAdminStore(ctx, *cfgPath, opts)
	if err != nil {
		return err
	}
	defer func() { _ = store.Close() }()
	e, err := store.RotateEpoch(ctx, *maxIndices, time.Now())
	if err != nil {
		return err
	}
	fmt.Fprintln(out, "opened epoch", e.Number, "max_indices", e.MaxIndices)
	return nil
}

func runAdminListPending(args []string, opts Options, out io.Writer) error {
	fs := flag.NewFlagSet("admin list-pending", flag.ContinueOnError)
	cfgPath := fs.String("config", "", "path to issuer.yaml")
	statusFlag := fs.String("status", "", "filter by status (new|approved|rejected)")
	limit := fs.Int("limit", 0, "max rows to print")
	if err := fs.Parse(args); err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	store, err := openAdminStore(ctx, *cfgPath, opts)
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
