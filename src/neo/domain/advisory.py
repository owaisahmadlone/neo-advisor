"""Client-visible advisory contract (versioned; JSON Schema exported from these models).

What planners see: set-points in operator units, the value at stake, the binding limits
and the reasons. What they do not see: solver details, penalty weights, back-off
margins and calibration internals; those stay in the run manifest.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

SCHEMA_VERSION = "1.0"

Action = Literal["increase", "decrease", "maintain", "open", "open_fully", "shut_in", "unavailable"]
Status = Literal["ready", "ready_with_flags", "blocked"]
Confidence = Literal["high", "medium", "low"]


class Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class StatusReason(Model):
    code: str
    message: str
    subject: str = ""


class WellReason(Model):
    key: str
    label: str
    usd_per_mmscfd: float


class WellAdvice(Model):
    item_id: str
    well_id: str
    platform: str
    action: Action
    current_rate_mmscfd: float
    recommended_rate_mmscfd: float
    implied_choke_pct: float | None
    wellhead_pressure_bara: float | None
    rank: int | None
    merit_status: str
    unit_value_usd_per_mmscfd: float | None
    total_contribution_usd_d: float | None
    reasons: list[WellReason]
    confidence: Confidence
    flags: list[str]
    executing_role: str = "PTOCC / FOM"


class NetworkAdvice(Model):
    suction_setpoint_bara: float
    trains_online: int
    trains_available: int
    routes: dict[str, str]
    hub_inflow_mmscfd: float
    compression_ullage_mmscfd: float
    discharge_pressure_bara: float
    arrival_pressure_bara: float


class SalesGas(Model):
    volume_mmscfd: float
    ghv_btu_scf: float
    co2_mol_pct: float
    co2_removal_mmscfd: float


class ContractAllocation(Model):
    contract_id: str
    buyer: str
    priority: int
    nominated_mmbtu_d: float
    planned_mmbtu_d: float
    shortfall_mmbtu_d: float


class ExcessAllocation(Model):
    outlet_id: str
    buyer: str
    planned_mmbtu_d: float


class ValueLine(Model):
    component: str
    plan_usd_d: float
    baseline_usd_d: float
    delta_usd_d: float


class BindingConstraint(Model):
    key: str
    label: str
    shadow_value: float
    unit: str


class ValueRange(Model):
    p10_usd_d: float
    p50_usd_d: float
    p90_usd_d: float


class Advisory(Model):
    schema_version: str = SCHEMA_VERSION
    run_id: str
    revision: int
    supersedes: str | None
    gas_day: date
    valid_from_utc: datetime
    valid_to_utc: datetime
    snapshot_cutoff_utc: datetime
    status: Status
    status_reasons: list[StatusReason]
    wells: list[WellAdvice]
    network: NetworkAdvice | None
    sales_gas: SalesGas | None
    allocations: list[ContractAllocation]
    excess: list[ExcessAllocation]
    value_waterfall: list[ValueLine]
    plan_value_usd_d: ValueRange | None
    marginal_gas_value_usd_mmbtu: float | None
    binding_constraints: list[BindingConstraint]
    choke_back_order: list[str] = []  # if a network limit tightens intraday, choke these first, in order
    data_flags: list[StatusReason]


class DecisionRecord(Model):
    """One planner decision on one advisory item (written by the review UI, not by NeO)."""

    run_id: str
    item_id: str
    decision: Literal["approve", "edit", "reject"]
    reason_code: str
    user: str
    decided_utc: datetime
    edited_value: float | None = None
