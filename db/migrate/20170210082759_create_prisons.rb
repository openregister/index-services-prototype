class CreatePrisons < ActiveRecord::Migration[5.0]
  def change
    create_table :prisons do |t|
      t.string :name
      t.string :code
      t.string :organisation
      t.string :address
      t.date :closed
      t.date :opened

      t.timestamps
    end
  end
end
