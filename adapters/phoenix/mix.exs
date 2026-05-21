defmodule BugFab.MixProject do
  use Mix.Project

  @version "0.1.0"
  @source_url "https://github.com/AZgeekster/Bug-Fab"

  def project do
    [
      app: :bug_fab,
      version: @version,
      elixir: "~> 1.16",
      elixirc_paths: elixirc_paths(Mix.env()),
      start_permanent: Mix.env() == :prod,
      deps: deps(),
      description: description(),
      package: package(),
      name: "bug_fab",
      source_url: @source_url,
      docs: docs(),
      aliases: aliases(),
      preferred_cli_env: [test: :test]
    ]
  end

  def application do
    [
      extra_applications: [:logger, :crypto],
      mod: {BugFab.Application, []}
    ]
  end

  defp elixirc_paths(:test), do: ["lib", "test/support"]
  defp elixirc_paths(_), do: ["lib"]

  defp deps do
    [
      # Mandatory — Plug.Router for mountable endpoints.
      {:plug, "~> 1.15"},
      {:plug_cowboy, "~> 2.7"},
      {:jason, "~> 1.4"},

      # Optional Ecto backend — the schemas and migration are always loaded
      # but consumers who use FileStorage do not need a database.
      {:ecto, "~> 3.11", optional: true},
      {:ecto_sql, "~> 3.11", optional: true},
      {:postgrex, "~> 0.17", optional: true},
      {:ecto_sqlite3, "~> 0.13", optional: true},

      # Test-only.
      {:floki, "~> 0.36", only: :test},
      {:ex_doc, "~> 0.31", only: :dev, runtime: false}
    ]
  end

  defp description do
    """
    Mountable Plug router implementing the Bug-Fab v0.1 wire protocol for
    Phoenix / Plug applications. Provides in-app bug reporting with
    screenshot, browser context, and lifecycle workflow — backend-agnostic
    (file or Ecto storage).
    """
  end

  defp package do
    [
      maintainers: ["Bug-Fab contributors"],
      licenses: ["MIT"],
      links: %{"GitHub" => @source_url},
      files: ~w(lib priv/repo priv/static mix.exs README.md MIGRATION_NOTES.md LICENSE.txt)
    ]
  end

  defp docs do
    [
      main: "readme",
      extras: ["README.md", "MIGRATION_NOTES.md"]
    ]
  end

  defp aliases do
    [
      "ecto.setup": ["ecto.create", "ecto.migrate"],
      "ecto.reset": ["ecto.drop", "ecto.setup"],
      test: ["test"]
    ]
  end
end
