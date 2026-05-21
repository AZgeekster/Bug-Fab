# frozen_string_literal: true

require_relative "lib/bug_fab/version"

Gem::Specification.new do |spec|
  spec.name        = "bug_fab-rails"
  spec.version     = BugFab::VERSION
  spec.authors     = ["Bug-Fab contributors"]
  spec.email       = ["noreply@example.com"]

  spec.summary     = "Mountable Rails Engine implementing the Bug-Fab v0.1 wire protocol."
  spec.description = <<~DESC
    bug_fab-rails is a Rails Engine adapter for Bug-Fab — a framework-agnostic
    bug-reporting tool. Mount the engine in routes.rb, run the install
    generator and migrations, and ship in-app bug reports with screenshots,
    annotations, and auto-captured browser context. Implements the Bug-Fab
    v0.1 wire protocol (see https://github.com/AZgeekster/Bug-Fab).
  DESC

  spec.homepage = "https://github.com/AZgeekster/Bug-Fab"
  spec.license  = "MIT"

  spec.metadata["homepage_uri"]     = spec.homepage
  spec.metadata["source_code_uri"]  = spec.homepage
  spec.metadata["bug_tracker_uri"]  = "#{spec.homepage}/issues"
  spec.metadata["documentation_uri"] = "#{spec.homepage}/blob/main/docs/PROTOCOL.md"
  spec.metadata["rubygems_mfa_required"] = "true"

  spec.required_ruby_version = ">= 3.2"

  spec.files = Dir[
    "{app,config,db,lib}/**/*",
    "MIT-LICENSE",
    "LICENSE.txt",
    "Rakefile",
    "README.md"
  ]

  spec.add_dependency "rails", ">= 7.1", "< 9.0"

  spec.add_development_dependency "sqlite3", "~> 1.7"
  spec.add_development_dependency "minitest", "~> 5.20"
  spec.add_development_dependency "rake", "~> 13.0"
  spec.add_development_dependency "rubocop", "~> 1.60"
  spec.add_development_dependency "rubocop-rails", "~> 2.23"
end
