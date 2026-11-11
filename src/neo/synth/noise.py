"""Measurement-error model for the landing zone (sizes from plan section 1.5)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class NoiseSpec:
    """Standard deviations unless named otherwise; relative values are fractions of the reading."""

    pressure_span_bar: float = 150.0
    pressure_sd_of_span: float = 0.0015
    pressure_drift_sd_bar_per_day: float = 0.005
    temperature_sd_c: float = 0.5
    meter_bias_sd: float = 0.01
    meter_sd: float = 0.01
    fiscal_bias_sd: float = 0.003
    fiscal_sd: float = 0.003
    vfm_bias_sd: float = 0.03
    vfm_sd: float = 0.02
    analyser_sd_mol_pct: float = 0.1
    ghv_sd: float = 0.002
    choke_sd_pct: float = 0.3
    dropout_fraction: float = 0.01
    bad_fraction: float = 0.005
    stuck_probability: float = 0.02  # per non-critical analog tag per gas day
    stuck_samples: tuple[int, int] = (12, 48)
    test_gas_sd: float = 0.04
    test_liquid_sd: float = 0.10
    test_pwh_sd_bar: float = 0.3
    test_co2_sd_mol_pct: float = 0.3
    test_reject_fraction: float = 0.05
    lab_co2_sd_mol_pct: float = 0.2
    lab_rel_sd: float = 0.10
    lab_ghv_sd: float = 0.005
    third_party_declared_swing: float = 0.05
    third_party_missing_fraction: float = 0.10
    water_meter_sd: float = 0.03

    @property
    def pressure_sd_bar(self) -> float:
        return self.pressure_sd_of_span * self.pressure_span_bar

    @classmethod
    def clean(cls) -> NoiseSpec:
        """Unbiased instruments with no data-quality faults.

        A floor of white noise stays: real analog signals never repeat bit for bit, and the
        data gate reads an exactly flat signal as a stuck sensor.
        """
        return cls(
            pressure_sd_of_span=1e-5,
            pressure_drift_sd_bar_per_day=0.0,
            temperature_sd_c=0.01,
            meter_bias_sd=0.0,
            meter_sd=1e-4,
            fiscal_bias_sd=0.0,
            fiscal_sd=1e-4,
            vfm_bias_sd=0.0,
            vfm_sd=1e-4,
            analyser_sd_mol_pct=1e-3,
            ghv_sd=1e-5,
            choke_sd_pct=0.01,
            dropout_fraction=0.0,
            bad_fraction=0.0,
            stuck_probability=0.0,
            test_gas_sd=0.0,
            test_liquid_sd=0.0,
            test_pwh_sd_bar=0.0,
            test_co2_sd_mol_pct=0.0,
            test_reject_fraction=0.0,
            lab_co2_sd_mol_pct=0.0,
            lab_rel_sd=0.0,
            lab_ghv_sd=0.0,
            third_party_declared_swing=0.0,
            third_party_missing_fraction=0.0,
            water_meter_sd=0.0,
        )
