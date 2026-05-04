# AuctionObjects.py. Claude guided by JFR, 2026 04 21.
# Copyright 2026 John F. Raffensperger. All rights reserved. Unauthorised copying or redistribution is prohibited.
# Purpose: Define Pydantic models for market inputs and outputs.

from __future__ import annotations
from pydantic import BaseModel, Field

class AuctionPeriod(BaseModel):
	id: int
	label: str

class Auction(BaseModel):
	id: int
	status: str
	periods: list[AuctionPeriod]
	first_water_take_date: str | None = None
	last_water_take_date: str | None = None
	period_length_hours: int | None = None
	auction_type: str | None = None
	created_date: str | None = None
	closed_date: str | None = None
	solve_status: str | None = None
	objective_value: float | None = None

class Trader(BaseModel):
	id: int
	name: str
	allocation_by_period: dict[int, float]

class Well(BaseModel):
	id: int
	name: str
	participant_id: int
	gw_model_layer: int | None = None
	gw_model_row: int | None = None
	gw_model_column: int | None = None
	latitude: float | None = None
	longitude: float | None = None

class ControlPoint(BaseModel):
	id: int
	name: str
	bound_by_period: dict[int, float]
	gw_model_row: int | None = None
	gw_model_column: int | None = None
	latitude: float | None = None
	longitude: float | None = None

class ResponseFactor(BaseModel):
	well_id: int
	control_point_id: int
	pumping_period: int
	effect_period: int
	value: float

class BidSegment(BaseModel):
	id: str
	participant_id: int
	well_id: int
	period_id: int
	quantity: float = Field(gt=0)
	price: float = Field(gt=0)
	submitted_at: str | None = None

class RightsConversion(BaseModel):
	policy_name: str
	summary: str

class AuctionCase(BaseModel):
	catchment_name: str
	source_note: str
	current_participant_id: int
	auction: Auction
	participants: list[Trader]
	wells: list[Well]
	control_points: list[ControlPoint]
	response_factors: list[ResponseFactor]
	bids: list[BidSegment]
	rights_conversion: RightsConversion

class AcceptedBid(BaseModel):
	bid_id: str
	accepted_quantity: float

class TraderPeriodResult(BaseModel):
	participant_id: int
	period_id: int
	accepted_quantity: float
	initial_allocation: float

class ControlPointResult(BaseModel):
	control_point_id: int
	period_id: int
	used_capacity: float
	bound_capacity: float
	dual_value: float

class MarketResult(BaseModel):
	solve_status: str
	objective_value: float
	accepted_bids: list[AcceptedBid]
	trader_period_results: list[TraderPeriodResult]
	control_point_results: list[ControlPointResult]
	well_period_prices: dict[tuple[int, int], float]  # key: (well_id, period_id), value: dual of quantity equality constraint
