# frozen_string_literal: true

module BugFab
  # Gem version. Distinct from the wire-protocol version (`PROTOCOL_VERSION`).
  VERSION = "0.1.0"

  # The Bug-Fab wire-protocol version this adapter implements. Adapters MUST
  # reject submissions whose `protocol_version` does not match this value.
  PROTOCOL_VERSION = "0.1"
end
