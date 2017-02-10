Rails.application.routes.draw do
  get 'prisions/index'

  root to: 'prisons#index'

  resources :prisons

  get 'combined-lists', to: 'prisons#combine', as: 'combine'
end
