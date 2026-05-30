// SPDX-License-Identifier: MIT

// Package cmdrun is the shared command-line surface for the Shadownet
// Provider reference servers. Both the SQLite-backed cmd/provider-server
// in the main core module and the Postgres-backed
// cmd/provider-server-pg in the pgstore submodule wire their store
// implementation in through Options and call Main.
//
// All flag parsing, YAML config loading, signing-key loading, signal
// handling, and admin subcommands live here so the two cmd binaries stay
// thin shims.
package cmdrun

import (
	"context"
	"crypto/ed25519"
	"crypto/tls"
	"errors"
	"flag"
	"fmt"
	"io"
	"log/slog"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"github.com/shadownet-protocol/shadownet/core/internal/config"
	"github.com/shadownet-protocol/shadownet/core/internal/crypto"
	"github.com/shadownet-protocol/shadownet/core/internal/identifiers"
	"github.com/shadownet-protocol/shadownet/core/internal/keyguard"
	"github.com/shadownet-protocol/shadownet/core/internal/provider"
)

// Options is the per-binary wiring. Callers supply the storage driver
// they accept and a constructor that opens it from a parsed DSN.
type Options struct {
	// BinaryName is the program name used in error messages and usage
	// output ("provider-server" or "provider-server-pg").
	BinaryName string
	// StorageDriver is the only `storage.driver` value the binary will
	// accept ("sqlite" or "postgres"). Configs that name a different
	// driver are rejected with a clear error.
	StorageDriver string
	// OpenStore opens the store from the YAML config's storage.dsn. The
	// returned Closer is invoked when the binary exits.
	OpenStore func(ctx context.Context, dsn string) (provider.Store, io.Closer, error)
}

type fileConfig struct {
	Listen      string        `yaml:"listen"`
	Domain      string        `yaml:"domain"`
	CacheMaxAge int           `yaml:"cacheMaxAge"`
	DNSEndpoint string        `yaml:"dnsEndpoint"`
	Issuer      bool          `yaml:"issuer"`
	Signing     signingBlock  `yaml:"signing"`
	Storage     storageBlock  `yaml:"storage"`
	TLS         tlsBlock      `yaml:"tls"`
	Shutdown    time.Duration `yaml:"shutdownTimeout"`
}

type signingBlock struct {
	Keyfile string `yaml:"keyfile"`
}

type storageBlock struct {
	Driver string `yaml:"driver"`
	DSN    string `yaml:"dsn"`
}

type tlsBlock struct {
	Cert string `yaml:"cert"`
	Key  string `yaml:"key"`
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
	case "dns-record":
		return runDNSRecord(sub, opts, os.Stdout)
	case "admin":
		if len(sub) == 0 {
			return fmt.Errorf("%s: admin <add|remove|list> required", opts.BinaryName)
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
	fmt.Fprintf(w, `%s — Shadownet Provider HTTP reference (RFC 0001 §5.2)

Usage:
  %[1]s serve       --config <provider.yaml>
  %[1]s dns-record  --config <provider.yaml> [--issuer]
  %[1]s admin add    --config <provider.yaml> --local <name> --pk <z6Mk...> --a2a-url <url> [--display <name>] [--description <text>]
  %[1]s admin remove --config <provider.yaml> --local <name>
  %[1]s admin list   --config <provider.yaml>
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
	if c.Domain == "" {
		return fileConfig{}, errors.New("config.domain required")
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
	return c, nil
}

func loadSigner(path string) (crypto.KeyPair, error) {
	kp, err := crypto.LoadKeyFile(path)
	if err != nil {
		return crypto.KeyPair{}, fmt.Errorf("load signing key: %w", err)
	}
	if err := keyguard.AssertNotFixture(kp.Public, "provider"); err != nil {
		return crypto.KeyPair{}, err
	}
	return kp, nil
}

func runServe(args []string, opts Options) error {
	fs := flag.NewFlagSet("serve", flag.ContinueOnError)
	cfgPath := fs.String("config", "", "path to provider.yaml")
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

	store, closer, err := opts.OpenStore(ctx, cfg.Storage.DSN)
	if err != nil {
		return err
	}
	defer func() { _ = closer.Close() }()

	logger := slog.New(slog.NewJSONHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelInfo}))

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
		"domain", cfg.Domain,
		"storage", opts.StorageDriver,
		"tls", tlsConfig != nil,
	)

	return provider.Run(ctx, provider.RunConfig{
		ListenAddr:      cfg.Listen,
		ProviderDomain:  cfg.Domain,
		Signer:          kp.Private,
		Store:           store,
		CacheMaxAge:     cfg.CacheMaxAge,
		ShutdownTimeout: cfg.Shutdown,
		Logger:          logger,
		TLSConfig:       tlsConfig,
	})
}

func runDNSRecord(args []string, opts Options, out io.Writer) error {
	fs := flag.NewFlagSet("dns-record", flag.ContinueOnError)
	cfgPath := fs.String("config", "", "path to provider.yaml")
	issuer := fs.Bool("issuer", false, "include iss=true (this domain also operates an issuer)")
	if err := fs.Parse(args); err != nil {
		return err
	}
	cfg, err := loadFileConfig(*cfgPath, opts)
	if err != nil {
		return err
	}
	if cfg.DNSEndpoint == "" {
		return errors.New("config.dnsEndpoint required for dns-record (e.g. https://shadow.example.com/v1)")
	}
	kp, err := loadSigner(cfg.Signing.Keyfile)
	if err != nil {
		return err
	}
	var extras []string
	if *issuer || cfg.Issuer {
		extras = append(extras, "iss=true")
	}
	txt, err := provider.TXTRecord(cfg.DNSEndpoint, []ed25519.PublicKey{kp.Public}, extras...)
	if err != nil {
		return err
	}
	fmt.Fprintln(out, txt)
	return nil
}

func runAdmin(sub string, args []string, opts Options, out io.Writer) error {
	switch sub {
	case "add":
		return runAdminAdd(args, opts, out)
	case "remove":
		return runAdminRemove(args, opts, out)
	case "list":
		return runAdminList(args, opts, out)
	default:
		return fmt.Errorf("unknown admin subcommand %q (want add|remove|list)", sub)
	}
}

func openAdminStore(ctx context.Context, cfg fileConfig, opts Options) (provider.Store, io.Closer, error) {
	return opts.OpenStore(ctx, cfg.Storage.DSN)
}

func runAdminAdd(args []string, opts Options, out io.Writer) error {
	fs := flag.NewFlagSet("admin add", flag.ContinueOnError)
	cfgPath := fs.String("config", "", "path to provider.yaml")
	local := fs.String("local", "", "Shadowname local part (e.g. alice)")
	pk := fs.String("pk", "", "Shadow's multibase Ed25519 public key (z6Mk…)")
	a2a := fs.String("a2a-url", "", "Shadow's A2A endpoint URL")
	display := fs.String("display", "", "optional display name")
	desc := fs.String("description", "", "optional card description")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if *local == "" || *pk == "" || *a2a == "" {
		return errors.New("admin add: --local, --pk, --a2a-url all required")
	}
	if identifiers.Classify(*pk) != identifiers.ClassPubKey {
		return fmt.Errorf("admin add: %q is not a valid multibase Ed25519 pubkey", *pk)
	}
	cfg, err := loadFileConfig(*cfgPath, opts)
	if err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	store, closer, err := openAdminStore(ctx, cfg, opts)
	if err != nil {
		return err
	}
	defer func() { _ = closer.Close() }()
	rec := provider.Record{
		Local:           strings.ToLower(*local),
		ShadowPublicKey: *pk,
		A2AURL:          *a2a,
		DisplayName:     *display,
		Description:     *desc,
	}
	if err := store.PutRecord(ctx, rec); err != nil {
		return err
	}
	fmt.Fprintln(out, "added", rec.Local+"@"+cfg.Domain)
	return nil
}

func runAdminRemove(args []string, opts Options, out io.Writer) error {
	fs := flag.NewFlagSet("admin remove", flag.ContinueOnError)
	cfgPath := fs.String("config", "", "path to provider.yaml")
	local := fs.String("local", "", "Shadowname local part")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if *local == "" {
		return errors.New("admin remove: --local required")
	}
	cfg, err := loadFileConfig(*cfgPath, opts)
	if err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	store, closer, err := openAdminStore(ctx, cfg, opts)
	if err != nil {
		return err
	}
	defer func() { _ = closer.Close() }()
	if err := store.DeleteRecord(ctx, strings.ToLower(*local)); err != nil {
		return err
	}
	fmt.Fprintln(out, "removed", *local)
	return nil
}

func runAdminList(args []string, opts Options, out io.Writer) error {
	fs := flag.NewFlagSet("admin list", flag.ContinueOnError)
	cfgPath := fs.String("config", "", "path to provider.yaml")
	if err := fs.Parse(args); err != nil {
		return err
	}
	cfg, err := loadFileConfig(*cfgPath, opts)
	if err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	store, closer, err := openAdminStore(ctx, cfg, opts)
	if err != nil {
		return err
	}
	defer func() { _ = closer.Close() }()
	records, err := store.ListRecords(ctx)
	if err != nil {
		return err
	}
	if len(records) == 0 {
		fmt.Fprintln(out, "(no records)")
		return nil
	}
	for _, r := range records {
		fmt.Fprintf(out, "%s@%s\t%s\t%s\n", r.Local, cfg.Domain, r.ShadowPublicKey, r.A2AURL)
	}
	return nil
}
