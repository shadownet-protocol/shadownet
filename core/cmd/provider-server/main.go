// SPDX-License-Identifier: MIT

// Command provider-server is the Shadownet Provider HTTP reference server
// (RFC 0001 §5.2). It hosts signed A2A AgentCards at <ep>/identity/<local>
// for the Shadownames registered in its store, and exposes admin subcommands
// for record CRUD and DNS TXT generation.
//
// Subcommands:
//
//	provider-server serve --config provider.yaml
//	provider-server dns-record --config provider.yaml [--issuer]
//	provider-server admin add    --config provider.yaml --local alice --pk z6Mk... --a2a-url https://...
//	provider-server admin remove --config provider.yaml --local alice
//	provider-server admin list   --config provider.yaml
//
// Config file (YAML):
//
//	listen: 127.0.0.1:8443
//	domain: sh4dow.org
//	cacheMaxAge: 3600
//	signing:
//	  keyfile: ./provider.jwk
//	storage:
//	  driver: sqlite
//	  dsn: ./provider.db
//	tls:
//	  cert: ./tls.crt        # optional; omit for plaintext (loopback only)
//	  key:  ./tls.key
//	dnsEndpoint: https://shadow.sh4dow.org/v1   # printed by `dns-record`
package main

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
	"github.com/shadownet-protocol/shadownet/core/internal/provider/sqlitestore"
)

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
	case "dns-record":
		err = runDNSRecord(args, os.Stdout)
	case "admin":
		if len(args) == 0 {
			err = errors.New("provider-server: admin <add|remove|list> required")
		} else {
			err = runAdmin(args[0], args[1:], os.Stdout)
		}
	case "version", "--version", "-v":
		fmt.Fprintln(os.Stdout, "provider-server (dev) (protocol v0.2)")
	case "-h", "--help":
		usage(os.Stdout)
	default:
		usage(os.Stderr)
		os.Exit(2)
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "provider-server:", err)
		os.Exit(1)
	}
}

func usage(w io.Writer) {
	fmt.Fprintln(w, `provider-server — Shadownet Provider HTTP reference (RFC 0001 §5.2)

Usage:
  provider-server serve       --config <provider.yaml>
  provider-server dns-record  --config <provider.yaml> [--issuer]
  provider-server admin add    --config <provider.yaml> --local <name> --pk <z6Mk...> --a2a-url <url> [--display <name>] [--description <text>]
  provider-server admin remove --config <provider.yaml> --local <name>
  provider-server admin list   --config <provider.yaml>
  provider-server version`)
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
	if c.Domain == "" {
		return fileConfig{}, errors.New("config.domain required")
	}
	if c.Signing.Keyfile == "" {
		return fileConfig{}, errors.New("config.signing.keyfile required")
	}
	if c.Storage.Driver == "" {
		c.Storage.Driver = "sqlite"
	}
	if c.Storage.Driver != "sqlite" {
		return fileConfig{}, fmt.Errorf("config.storage.driver %q not supported in this build (sqlite only)", c.Storage.Driver)
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

func runServe(args []string) error {
	fs := flag.NewFlagSet("serve", flag.ContinueOnError)
	cfgPath := fs.String("config", "", "path to provider.yaml")
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
	store, err := sqlitestore.Open(cfg.Storage.DSN)
	if err != nil {
		return err
	}
	defer func() { _ = store.Close() }()

	logger := slog.New(slog.NewJSONHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelInfo}))

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
		"provider-server starting",
		"listen", cfg.Listen,
		"domain", cfg.Domain,
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

func runDNSRecord(args []string, out io.Writer) error {
	fs := flag.NewFlagSet("dns-record", flag.ContinueOnError)
	cfgPath := fs.String("config", "", "path to provider.yaml")
	issuer := fs.Bool("issuer", false, "include iss=true (this domain also operates an issuer)")
	if err := fs.Parse(args); err != nil {
		return err
	}
	cfg, err := loadFileConfig(*cfgPath)
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

func runAdmin(sub string, args []string, out io.Writer) error {
	switch sub {
	case "add":
		return runAdminAdd(args, out)
	case "remove":
		return runAdminRemove(args, out)
	case "list":
		return runAdminList(args, out)
	default:
		return fmt.Errorf("unknown admin subcommand %q (want add|remove|list)", sub)
	}
}

func runAdminAdd(args []string, out io.Writer) error {
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
	cfg, err := loadFileConfig(*cfgPath)
	if err != nil {
		return err
	}
	store, err := sqlitestore.Open(cfg.Storage.DSN)
	if err != nil {
		return err
	}
	defer func() { _ = store.Close() }()
	rec := provider.Record{
		Local:           strings.ToLower(*local),
		ShadowPublicKey: *pk,
		A2AURL:          *a2a,
		DisplayName:     *display,
		Description:     *desc,
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := store.PutRecord(ctx, rec); err != nil {
		return err
	}
	fmt.Fprintln(out, "added", rec.Local+"@"+cfg.Domain)
	return nil
}

func runAdminRemove(args []string, out io.Writer) error {
	fs := flag.NewFlagSet("admin remove", flag.ContinueOnError)
	cfgPath := fs.String("config", "", "path to provider.yaml")
	local := fs.String("local", "", "Shadowname local part")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if *local == "" {
		return errors.New("admin remove: --local required")
	}
	cfg, err := loadFileConfig(*cfgPath)
	if err != nil {
		return err
	}
	store, err := sqlitestore.Open(cfg.Storage.DSN)
	if err != nil {
		return err
	}
	defer func() { _ = store.Close() }()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := store.DeleteRecord(ctx, strings.ToLower(*local)); err != nil {
		return err
	}
	fmt.Fprintln(out, "removed", *local)
	return nil
}

func runAdminList(args []string, out io.Writer) error {
	fs := flag.NewFlagSet("admin list", flag.ContinueOnError)
	cfgPath := fs.String("config", "", "path to provider.yaml")
	if err := fs.Parse(args); err != nil {
		return err
	}
	cfg, err := loadFileConfig(*cfgPath)
	if err != nil {
		return err
	}
	store, err := sqlitestore.Open(cfg.Storage.DSN)
	if err != nil {
		return err
	}
	defer func() { _ = store.Close() }()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
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
