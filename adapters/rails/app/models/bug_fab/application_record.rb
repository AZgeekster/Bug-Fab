# frozen_string_literal: true

module BugFab
  # Abstract base for every Bug-Fab model. Inherits from the host's
  # ActiveRecord::Base so engine tables live alongside host tables on the
  # primary connection by default.
  #
  # Consumers wanting Bug-Fab's tables on a separate database can override
  # `connects_to` here in their host app via reopening the class:
  #
  #     # config/initializers/bug_fab_db.rb
  #     BugFab::ApplicationRecord.connects_to database: { writing: :bug_fab, reading: :bug_fab }
  class ApplicationRecord < ::ActiveRecord::Base
    self.abstract_class = true
  end
end
