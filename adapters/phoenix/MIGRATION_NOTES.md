# Migration Notes — BEAM / OTP specifics

Things future consumers should know that don't fit in the README.

## Supervision tree

`BugFab.Application` starts a single child: `BugFab.RateLimiter` (an ETS-backed GenServer). It does NOT start any Repo, Cowboy listener, or storage GenServer.

* **Ecto Repo lifecycle** — the host application is responsible for starting the Repo. `BugFab.Storage.EctoStorage` reads the Repo module from config and uses it; it does not start or stop the Repo itself.
* **FileStorage GenServer** — started lazily on first request via `BugFab.storage_handle/0` and cached in `:persistent_term`. It is NOT linked to the application supervisor — if it crashes mid-request, the next request will start a fresh one. This is acceptable for v0.1 because `FileStorage` operations are short and the state on disk is durable; for stricter availability semantics, supervise it yourself via `BugFab.Storage.FileStorage.Server.start_link/1` and override `BugFab.storage_handle/0`.

## Releases (`mix release`)

* Set storage paths via `config/runtime.exs`, NOT compile-time `config/config.exs`. Compile-time paths bake into the release and can't be overridden without re-building.
* The `priv/repo/migrations/` directory is included in the release artifact when `mix.exs` lists it in `package[:files]`. Run migrations from the release boot script:
  ```
  bin/my_app eval 'MyApp.Release.migrate()'
  ```
  where `MyApp.Release.migrate/0` uses `Ecto.Migrator` to run pending migrations from the `bug_fab` priv directory:
  ```elixir
  Ecto.Migrator.run(MyApp.Repo, :code.priv_dir(:bug_fab) |> Path.join("repo/migrations"), :up, all: true)
  ```

## Multi-node FileStorage caveat

`BugFab.Storage.FileStorage` serializes writes through a single GenServer **per node**. Two nodes pointed at the same shared NFS / EFS directory will race on `index.json` and lose writes. Use `EctoStorage` for multi-node deployments, or stand up one writer node and read-only replicas if you really want filesystem storage.

For single-node clustered Phoenix apps (one OS process), the GenServer is sufficient.

## Hot code upgrades

The cached storage handle lives in `:persistent_term` and is keyed by `{module, opts}`. After a hot code upgrade that changes the storage configuration, call `BugFab.reset_storage_handle/0` from your release upgrade callback.

## LiveView viewer

The bundled HTML viewer is intentionally minimal (a single embedded template). A real consumer will typically replace it with a Phoenix LiveView page that consumes the JSON endpoints (`/reports`, `/reports/:id`, etc.). A LiveView viewer is a candidate for v0.2 of this adapter — defer until at least one consumer has integrated and has opinions about the desired UX.

The JSON endpoints are intentionally exhaustive so a LiveView replacement does not require any non-public surface.

## Plug pipelines and `Plug.Parsers`

The intake router declares its own `Plug.Parsers` (multipart + json) plug. If you mount it inside a Phoenix endpoint that already runs `Plug.Parsers`, the two parsers compose fine — the inner parser sees the body untouched because Phoenix uses `body_reader` semantics that defer reading until a parser claims the body.

Caveat: do NOT run `Plug.Parsers` with `:urlencoded` upstream of `BugFab.IntakeRouter` — that parser will refuse multipart and short-circuit. Either configure your endpoint's parser with `:multipart` in the list or restrict the urlencoded parser to a different pipeline.

## Rate limiter swap

The bundled `BugFab.RateLimiter` is a tiny ETS-based fixed-window limiter. For production deployments that already use `Hammer` or `PlugAttack`, you can disable the bundled limiter (`rate_limit_enabled: false`) and apply your own limiter as a plug upstream of `BugFab.IntakeRouter`. The protocol's `retry_after_seconds` field is the only response-shape contract — any limiter that emits it through the same JSON envelope is conformant.

## Telemetry (v0.2 placeholder)

This adapter does not yet emit `:telemetry` events. The intended event names mirror the Python reference adapter:

* `[:bug_fab, :report, :received]`
* `[:bug_fab, :status, :changed]`
* `[:bug_fab, :report, :deleted]`
* `[:bug_fab, :bulk, :close_fixed]`
* `[:bug_fab, :bulk, :archive_closed]`

Adding these is non-breaking and is queued for v0.2.

## GitHub Issues sync

Not yet wired in this adapter. The intake router has the integration hook (the `do_save` helper checks for a configured sync client) but the Tesla-based HTTP client itself is deferred to v0.2 to keep the dep footprint minimal for the first release. Consumers who need GitHub sync today can wire it as a `Task.start/1` from their host application's submission webhook, using the JSON detail returned by `GET /reports/:id`.
