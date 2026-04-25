# models.py. Claude guided by JFR, 2026 04 21.
# Purpose: Define Pydantic models for market inputs and outputs.

from __future__ import annotations
from pydantic import BaseModel, Field

class AuctionPeriod(BaseModel):
	id: str
	label: str

class Auction(BaseModel):
	id: str
	label: str
	bid_close_label: str
	status: str
	periods: list[AuctionPeriod]

class Participant(BaseModel):
	id: str
	name: str
	allocation_by_period: dict[str, float]

class Well(BaseModel):
	id: str
	name: str
	participant_id: str
	gw_model_row: int | None = None
	gw_model_column: int | None = None
	latitude: float | None = None
	longitude: float | None = None

class ControlPoint(BaseModel):
	id: str
	name: str
	bound_by_period: dict[str, float]
	gw_model_row: int | None = None
	gw_model_column: int | None = None
	latitude: float | None = None
	longitude: float | None = None

class ResponseFactor(BaseModel):
	well_id: str
	control_point_id: str
	pumping_period: str
	effect_period: str
	value: float

class BidSegment(BaseModel):
	id: str
	participant_id: str
	well_id: str
	period_id: str
	quantity: float = Field(gt=0)
	price: float = Field(gt=0)
	submitted_at: str | None = None

class RightsConversion(BaseModel):
	policy_name: str
	summary: str

class AuctionCase(BaseModel):
	catchment_name: str
	source_note: str
	current_participant_id: str
	auction: Auction
	participants: list[Participant]
	wells: list[Well]
	control_points: list[ControlPoint]
	response_factors: list[ResponseFactor]
	bids: list[BidSegment]
	rights_conversion: RightsConversion

class AcceptedBid(BaseModel):
	bid_id: str
	accepted_quantity: float

class TraderPeriodResult(BaseModel):
	participant_id: str
	period_id: str
	accepted_quantity: float
	initial_allocation: float

class ConstraintResult(BaseModel):
	control_point_id: str
	period_id: str
	used_capacity: float
	bound_capacity: float
	dual_value: float

class MarketResult(BaseModel):
	solve_status: str
	objective_value: float
	accepted_bids: list[AcceptedBid]
	trader_period_results: list[TraderPeriodResult]
	constraint_results: list[ConstraintResult]
	period_prices: dict[str, float]
