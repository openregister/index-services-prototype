class Prison < ApplicationRecord
  include PgSearch

  pg_search_scope(
    :search,
    against: %i(
      name
      address
    ),

    using: {
      tsearch: {
        dictionary: 'english'
      }
    }
  )
end
