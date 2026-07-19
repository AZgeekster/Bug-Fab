# frozen_string_literal: true

require "rails/engine"

module BugFab
  # Mountable Rails Engine. Isolates the namespace so models, controllers,
  # routes, helpers, and asset paths cannot collide with the host
  # application.
  #
  # Migrations declared under `db/migrate/` in this engine are picked up
  # automatically by the host's `db:migrate` task because we append the
  # engine's migrate path to `config.paths["db/migrate"]` below.
  class Engine < ::Rails::Engine
    isolate_namespace BugFab

    # Append the engine's migrations onto the host's migration paths so
    # consumers can run `bin/rails db:migrate` after `bundle install`
    # without a separate `bug_fab:install:migrations` step. The install
    # generator still exists for the initializer; migrations come along
    # for free.
    initializer "bug_fab.append_migrations" do |app|
      next if app.root.to_s == root.to_s

      config.paths["db/migrate"].expanded.each do |expanded_path|
        app.config.paths["db/migrate"] << expanded_path
        ActiveRecord::Migrator.migrations_paths << expanded_path
      end
    end

    # Vendored JS bundle ships at `app/assets/javascripts/bug_fab/bug-fab.js`.
    # The engine adds the asset path to the host's pipeline so consumers can
    # reference it as `<script src="/assets/bug_fab/bug-fab.js">` (Sprockets
    # builds) or import it directly from a Propshaft / esbuild pipeline.
    initializer "bug_fab.assets" do |app|
      next unless app.config.respond_to?(:assets)

      app.config.assets.paths << root.join("app", "assets", "javascripts").to_s
      app.config.assets.precompile += %w[bug_fab/bug-fab.js bug_fab/bug-fab.css]
    end

    # Tests boot the engine inside a host app under `test/dummy/` (created
    # by Rails' engine generator). The conformance test uses an inline
    # rack-test host — see `test/integration/conformance_test.rb`.
    config.generators do |g|
      g.test_framework :minitest
      g.assets false
      g.helper false
    end
  end
end
