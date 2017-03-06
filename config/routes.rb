Rails.application.routes.draw do
  get 'prisions/index'

  root to: 'prisons#index'

  resources :prisons
end
