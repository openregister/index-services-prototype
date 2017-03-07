class AddAddressUprnToPrisons < ActiveRecord::Migration[5.0]
  def change
    add_column :prisons, :address_uprn, :string
  end
end
