class PrisonsController < ApplicationController
  helper_method :sort_column, :sort_direction

  def index
    if params[:search]
      @prisons = Prison.search(params[:search]).order("#{sort_column} #{sort_direction}")
    else
      @prisons = Prison.order("#{sort_column} #{sort_direction}")
    end
  end

  def combine
    if params[:search]
      @prisons = Prison.search(params[:search]).order("#{sort_column} #{sort_direction}")
    else
      @prisons = Prison.order("#{sort_column} #{sort_direction}")
    end
  end

  private

  def sortable_columns
    ["name", "organisation", "code", "opened", "closed", "address"]
  end

  def sort_column
    sortable_columns.include?(params[:column]) ? params[:column] : "name"
  end

  def sort_direction
    ["asc", "desc"].include?(params[:direction]) ? params[:direction] : "asc"
  end
end
