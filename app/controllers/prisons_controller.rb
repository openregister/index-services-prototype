class PrisonsController < ApplicationController
  def index
    if params[:search]
      @prisons = Prison.search(params[:search])
    else
      @prisons = Prison.all
    end
  end
end
