"""In-silico experimentation — arithmetic that actually runs.

The stage that most invites faking. It is easy to have a model *describe* a
power analysis and produce a paragraph containing numbers; nothing in the output
distinguishes that from a computation. So this module contains no model calls at
all. The agent chooses parameters and interprets results; the numbers come from
here, deterministically, and the gate downstream refuses to advance a stage
whose `results` block is empty.

Three things are computed, in increasing order of how often they are skipped:

`power` / `required_n`   the ordinary two-group question, plus the longitudinal
                         form, where the unit that matters is the number of
                         subjects and the informative quantity is the *slope*
                         difference, not the group difference.
`power_curve`            where the design stops being rescued by more donors.
`negative_control`       whether the most likely confound produces the same
                         signal. For single-cell AD work that confound is
                         compositional: a shift in how many microglia there are,
                         with no change in what any microglia is doing, moves
                         every pseudobulk estimate. A design that cannot separate
                         those two will report a positive result either way.

Stdlib only — `statistics.NormalDist` for the tails, seeded `random` for the
Monte Carlo — so results are identical on every machine and the engine keeps its
single dependency.
"""

from __future__ import annotations

import random
from statistics import NormalDist, mean, stdev

_N = NormalDist()
DEFAULT_ALPHA = 0.05
DEFAULT_SEED = 20260823


# ── two-group ───────────────────────────────────────────────────────────────

def two_sample_power(n_per_group: int, effect_d: float, alpha: float = DEFAULT_ALPHA) -> float:
    """Power of a two-sided two-sample t-test, normal approximation."""
    if n_per_group < 2 or effect_d <= 0:
        return 0.0
    se = (2.0 / n_per_group) ** 0.5
    crit = _N.inv_cdf(1 - alpha / 2)
    ncp = effect_d / se
    return round(_N.cdf(ncp - crit) + _N.cdf(-ncp - crit), 4)


def required_n(effect_d: float, power: float = 0.8, alpha: float = DEFAULT_ALPHA) -> int:
    """Donors per group needed to reach `power` at effect size `effect_d`."""
    if effect_d <= 0:
        return 10 ** 6
    z_a = _N.inv_cdf(1 - alpha / 2)
    z_b = _N.inv_cdf(power)
    return max(2, int(round(2 * ((z_a + z_b) / effect_d) ** 2)))


def detectable_effect(n_per_group: int, power: float = 0.8,
                      alpha: float = DEFAULT_ALPHA) -> float:
    """Smallest effect this n can detect — the honest way to report a small study."""
    if n_per_group < 2:
        return float("inf")
    z_a = _N.inv_cdf(1 - alpha / 2)
    z_b = _N.inv_cdf(power)
    return round((z_a + z_b) * (2.0 / n_per_group) ** 0.5, 4)


# ── longitudinal ────────────────────────────────────────────────────────────

def longitudinal_power(n_per_group: int, n_timepoints: int, slope_difference: float,
                       sd_residual: float = 1.0, sd_random_slope: float = 0.5,
                       spacing: float = 1.0, alpha: float = DEFAULT_ALPHA) -> dict:
    """Power to detect a group difference in *slope* under a random-slope model.

    The quantity a converter-vs-stable design is actually powered on. Adding
    timepoints shrinks only the residual term; between-subject slope variance is
    untouched by them and can only be paid for with more subjects. That is why a
    dense-sampling design on twelve people does not rescue an underpowered study,
    and the number below says so quantitatively.
    """
    if n_per_group < 2 or n_timepoints < 3:
        return {"achieved": 0.0, "note": "needs >= 3 timepoints and >= 2 subjects per group"}

    times = [i * spacing for i in range(n_timepoints)]
    t_bar = sum(times) / len(times)
    sxx = sum((t - t_bar) ** 2 for t in times)

    var_slope_hat = sd_random_slope ** 2 + (sd_residual ** 2) / sxx
    se_diff = (2 * var_slope_hat / n_per_group) ** 0.5
    crit = _N.inv_cdf(1 - alpha / 2)
    ncp = abs(slope_difference) / se_diff
    power = round(_N.cdf(ncp - crit) + _N.cdf(-ncp - crit), 4)

    return {
        "achieved": power,
        "se_slope_difference": round(se_diff, 4),
        "variance_from_between_subject": round(sd_random_slope ** 2 / var_slope_hat, 3),
        "variance_from_residual": round((sd_residual ** 2 / sxx) / var_slope_hat, 3),
        "note": ("between-subject slope variance dominates: more timepoints will not help, "
                 "only more participants"
                 if sd_random_slope ** 2 > (sd_residual ** 2) / sxx else
                 "residual variance dominates: additional timepoints still buy precision"),
    }


def power_curve(effect_d: float, ns: list[int] | None = None,
                alpha: float = DEFAULT_ALPHA) -> list[dict]:
    ns = ns or [5, 10, 15, 20, 30, 40, 60, 80, 120, 200]
    return [{"n_per_group": n, "power": two_sample_power(n, effect_d, alpha)} for n in ns]


# ── negative control: the compositional confound ────────────────────────────

def compositional_confound(n_per_group: int = 30, base_fraction: float = 0.10,
                           case_fraction: float = 0.15, state_effect: float = 0.0,
                           marker_specificity: float = 3.0, donor_sd: float = 0.25,
                           sims: int = 2000, alpha: float = DEFAULT_ALPHA,
                           seed: int = DEFAULT_SEED) -> dict:
    """How often a pure abundance shift is read as a change in cell state.

    Simulates donors whose microglia are transcriptionally *identical* between
    groups, differing only in how many there are. Pseudobulk expression of a
    microglia-enriched marker is then compared between groups. Every rejection is
    a false positive by construction.

    `state_effect > 0` adds a real per-cell change on top, which shows the mirror
    problem: the two mechanisms are not separable in pseudobulk at all, so the
    result is the same either way.
    """
    rng = random.Random(seed)
    rejections = 0
    diffs = []
    crit = _N.inv_cdf(1 - alpha / 2)

    for _ in range(sims):
        def group(fraction: float, state: float) -> list[float]:
            out = []
            for _i in range(n_per_group):
                frac = min(0.95, max(0.0, rng.gauss(fraction, fraction * donor_sd)))
                # Pseudobulk = abundance-weighted mixture of an enriched cell
                # type against a background of 1.0.
                signal = frac * (marker_specificity + state) + (1 - frac) * 1.0
                out.append(signal + rng.gauss(0, 0.05))
            return out

        controls = group(base_fraction, 0.0)
        cases = group(case_fraction, state_effect)

        m1, m2 = mean(controls), mean(cases)
        s1, s2 = stdev(controls), stdev(cases)
        se = ((s1 ** 2 + s2 ** 2) / n_per_group) ** 0.5
        if se > 0 and abs(m2 - m1) / se > crit:
            rejections += 1
        diffs.append(m2 - m1)

    rate = rejections / sims
    return {
        "false_positive_rate": round(rate, 4),
        "mean_pseudobulk_shift": round(mean(diffs), 4),
        "abundance_shift": f"{base_fraction:.0%} -> {case_fraction:.0%}",
        "per_cell_state_effect": state_effect,
        "n_per_group": n_per_group,
        "sims": sims,
        "verdict": (
            "pseudobulk cannot separate abundance from state at this design: a purely "
            "compositional shift is called significant in "
            f"{rate:.0%} of simulations. Report abundance and per-cell state as separate "
            "outcomes, or the primary result is uninterpretable."
            if rate > 0.10 else
            f"compositional shift alone yields a {rate:.0%} rejection rate; the design "
            "tolerates an abundance change of this size."),
    }


# ── reanalysis feasibility ──────────────────────────────────────────────────

def reanalysis_feasibility(donor_counts: list[int], effect_d: float = 0.5,
                           alpha: float = DEFAULT_ALPHA) -> dict:
    """What the already-public data can support, before anyone collects more.

    Pooling is not free: each dataset contributes a study effect, so the honest
    figure is the pooled n with a study term, not the raw sum. The single largest
    dataset is reported alongside because pooling three small studies is often
    worse than analysing one adequate one.
    """
    usable = [n for n in donor_counts if isinstance(n, int) and n > 0]
    if not usable:
        return {"feasible": False, "reason": "no donor counts available on the latent datasets"}

    pooled = sum(usable)
    per_group = pooled // 2
    largest = max(usable)
    return {
        "feasible": True,
        "n_datasets": len(usable),
        "pooled_donors": pooled,
        "largest_single_dataset": largest,
        "power_pooled": two_sample_power(per_group, effect_d, alpha),
        "power_largest_alone": two_sample_power(largest // 2, effect_d, alpha),
        "required_n_per_group": required_n(effect_d, 0.8, alpha),
        "caveat": ("Pooled power assumes a study fixed effect is included. Without one, "
                   "the pooled estimate recovers batch structure rather than biology and "
                   "the apparent gain in n is spurious."),
    }


# ── stage entry point ───────────────────────────────────────────────────────

def run_simulations(plan: dict, seed: int = DEFAULT_SEED) -> dict:
    """Execute the simulations a plan implies. Returns the `results` block."""
    effect = float(plan.get("expected_effect_size") or 0.5)
    n_available = int(plan.get("n_available") or 0)
    per_group = max(2, n_available // 2)
    longitudinal = bool(plan.get("longitudinal"))
    alpha = float(plan.get("alpha") or DEFAULT_ALPHA)

    # Where the donors came from changes what the power number means. A figure
    # computed on donors that exist across the literature but have not been
    # assayed for this contrast is a projection, and labelling it as anything
    # else is the quiet way an autonomous pipeline starts overstating itself.
    n_source = plan.get("n_available_source") or "unspecified"
    projected = n_source != "reanalysable"

    results: dict = {
        "assumptions": {"expected_effect_size": effect, "alpha": alpha,
                        "n_available": n_available, "n_source": n_source,
                        "power_is_projected": projected, "seed": seed,
                        "design": "longitudinal" if longitudinal else "two-group"},
        "power_curve": power_curve(effect, alpha=alpha),
        "detectable_effect_at_available_n": detectable_effect(per_group, alpha=alpha),
    }

    if longitudinal:
        results["power"] = longitudinal_power(
            n_per_group=per_group,
            n_timepoints=int(plan.get("n_timepoints") or 4),
            slope_difference=effect,
            sd_random_slope=float(plan.get("sd_random_slope") or 0.5),
            alpha=alpha)
    else:
        results["power"] = {"achieved": two_sample_power(per_group, effect, alpha),
                            "n_per_group": per_group}

    if projected:
        results["power"]["conditional_on"] = (
            f"assaying {n_available} donors that are not currently available for this "
            f"contrast (source: {n_source}). This is a projection of what the design "
            f"would achieve, not what existing data supports.")
    results["required_n_per_group"] = required_n(effect, 0.8, alpha)
    results["negative_control"] = compositional_confound(
        n_per_group=per_group, alpha=alpha, seed=seed)

    latent = plan.get("latent_donor_counts") or []
    if latent:
        results["reanalysis"] = reanalysis_feasibility(latent, effect, alpha)
    return results
