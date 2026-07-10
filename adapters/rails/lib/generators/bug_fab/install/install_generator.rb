# frozen_string_literal: true

require "rails/generators"
require "rails/generators/base"

module BugFab
  module Generators
    # `bin/rails g bug_fab:install`
    #
    # Creates the initializer at `config/initializers/bug_fab.rb`. The
    # engine appends its own migrations to the host's migration paths
    # automatically (see `BugFab::Engine` initializer block), so consumers
    # do NOT need a separate `rails bug_fab:install:migrations` step.
    # `db:migrate` after this command picks up the engine tables.
    class InstallGenerator < Rails::Generators::Base
      source_root File.expand_path("templates", __dir__)

      desc "Create the Bug-Fab initializer in config/initializers/bug_fab.rb"

      def copy_initializer
        template "initializer.rb.tt", "config/initializers/bug_fab.rb"
      end

      def remind_about_migrations
        say "", :green
        say "Bug-Fab installed. Next steps:", :green
        say "  1. Review config/initializers/bug_fab.rb", :green
        say "  2. Run: bin/rails db:migrate", :green
        say "  3. Mount the engine in config/routes.rb:", :green
        say "       mount BugFab::Engine, at: '/bug-fab'", :green
        say "", :green
      end
    end
  end
end
