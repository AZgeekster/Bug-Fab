# frozen_string_literal: true

# Public entry point for the bug_fab-rails gem.
#
# Consumers add `gem "bug_fab-rails"` to their Gemfile, run
# `bundle install && bin/rails g bug_fab:install && bin/rails db:migrate`,
# then mount the engine in their `config/routes.rb`:
#
#     Rails.application.routes.draw do
#       mount BugFab::Engine, at: "/bug-fab"
#     end
#
# Configuration is performed in the generated initializer at
# `config/initializers/bug_fab.rb`. See {BugFab::Configuration} for the
# full option list.

require "bug_fab/version"
require "bug_fab/configuration"
require "bug_fab/errors"
require "bug_fab/validation"
require "bug_fab/github"
require "bug_fab/engine"

module BugFab
  class << self
    # Yield the singleton {Configuration} block to the consumer's initializer.
    #
    #     BugFab.configure do |config|
    #       config.storage_root = Rails.root.join("storage", "bug-fab")
    #       config.max_upload_mb = 10
    #     end
    def configure
      yield(configuration)
    end

    # Return the singleton {Configuration} instance, creating it on first
    # access. Consumers may also read individual options off this object
    # (e.g., `BugFab.configuration.storage_root`).
    def configuration
      @configuration ||= Configuration.new
    end

    # Replace the configuration wholesale. Primarily intended for tests that
    # want a clean slate between examples.
    def reset_configuration!
      @configuration = Configuration.new
    end
  end
end
