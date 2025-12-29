"""
Causal estimation utilities for "item necessity".

This implements a doubly-robust AIPW estimator with cross-fitting using
scikit-learn models (no specialized causal libraries required).
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np


class OverlapError(ValueError):
    """
    Raised when propensity overlap (positivity) is too poor to estimate reliably.

    Carries overlap diagnostics so callers can surface meaningful warnings.
    """

    def __init__(
        self,
        message: str,
        *,
        n: int,
        n_used: int,
        n_treated_used: int,
        n_control_used: int,
        trim_low: float | None,
        trim_high: float | None,
        e_min: float,
        e_p01: float,
        e_p50: float,
        e_p99: float,
        e_max: float,
        frac_trimmed: float,
    ) -> None:
        super().__init__(message)
        self.n = int(n)
        self.n_used = int(n_used)
        self.n_treated_used = int(n_treated_used)
        self.n_control_used = int(n_control_used)
        self.trim_low = trim_low
        self.trim_high = trim_high
        self.e_min = float(e_min)
        self.e_p01 = float(e_p01)
        self.e_p50 = float(e_p50)
        self.e_p99 = float(e_p99)
        self.e_max = float(e_max)
        self.frac_trimmed = float(frac_trimmed)


@dataclass(frozen=True, slots=True)
class AIPWConfig:
    n_splits: int = 2
    random_state: int = 42
    clip_eps: float = 1e-3
    trim_low: float | None = 0.05
    trim_high: float | None = 0.95


@dataclass(frozen=True, slots=True)
class AIPWEstimate:
    tau: float
    se: float
    ci95_low: float
    ci95_high: float
    p_value: float | None
    y1: float
    y0: float
    n: int
    n_treated: int
    n_control: int
    n_used: int
    e_min: float
    e_p01: float
    e_p50: float
    e_p99: float
    e_max: float
    frac_trimmed: float


def _normal_two_sided_p_value(z: float) -> float:
    # Two-sided p-value under N(0,1) using erfc (no scipy dependency).
    return float(math.erfc(abs(z) / math.sqrt(2.0)))


def e_value_from_risk_ratio(rr: float) -> float | None:
    """
    Compute the VanderWeele & Ding E-value for a risk ratio.

    If rr < 1 (protective effect), the E-value is computed on 1/rr.
    Returns None when rr is non-positive or non-finite.
    """
    try:
        rr = float(rr)
    except Exception:
        return None
    if not math.isfinite(rr) or rr <= 0:
        return None
    if rr < 1.0:
        rr = 1.0 / rr
    if rr <= 1.0:
        return 1.0
    return float(rr + math.sqrt(rr * (rr - 1.0)))


def _predict_proba_1(model, X) -> np.ndarray:
    if hasattr(model, "predict_proba"):
        p = model.predict_proba(X)
        return np.asarray(p)[:, 1]
    if hasattr(model, "decision_function"):
        logits = np.asarray(model.decision_function(X), dtype=np.float64)
        return 1.0 / (1.0 + np.exp(-logits))
    raise TypeError("Model does not support probability predictions")


def placements_to_outcome(placements: np.ndarray, outcome: str) -> tuple[np.ndarray, str]:
    """
    Convert placement (1..8) to an outcome vector.

    Returns:
      y: np.ndarray[float32]
      kind: "binary" | "continuous"
    """
    outcome = (outcome or "").strip().lower()
    p = placements.astype(np.int16, copy=False)
    if outcome in ("top4", "top_4", "topfour"):
        return (p <= 4).astype(np.float32), "binary"
    if outcome in ("win", "first", "1st"):
        return (p == 1).astype(np.float32), "binary"
    if outcome in ("rank_score", "rankscore", "score"):
        # Higher is better: 8 -> 0, 1 -> 7
        return (8 - p).astype(np.float32), "continuous"
    # Default: expected placement (lower is better)
    return p.astype(np.float32), "continuous"


def aipw_ate(
    X,
    T: np.ndarray,
    y: np.ndarray,
    *,
    kind: str,
    cfg: AIPWConfig,
) -> tuple[AIPWEstimate, np.ndarray, np.ndarray, np.ndarray]:
    """
    AIPW ATE with K-fold cross-fitting.

    Returns:
      estimate, phi, e_hat, used_mask
    """
    from sklearn.base import clone
    from sklearn.linear_model import SGDClassifier, SGDRegressor
    from sklearn.model_selection import KFold
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import MaxAbsScaler

    n = int(y.shape[0])
    if n == 0:
        raise ValueError("Empty sample")

    T = T.astype(np.int8, copy=False)
    y = y.astype(np.float32, copy=False)

    n_treated = int(T.sum())
    n_control = int(n - n_treated)
    if n_treated == 0 or n_control == 0:
        raise ValueError("Need both treated and control samples")

    n_splits = int(cfg.n_splits)
    if n_splits < 2:
        n_splits = 2
    n_splits = min(n_splits, 5, n)

    prop_model = make_pipeline(
        MaxAbsScaler(),
        SGDClassifier(
            loss="log_loss",
            penalty="l2",
            alpha=1e-4,
            max_iter=2000,
            tol=1e-3,
            random_state=cfg.random_state,
        ),
    )

    if kind == "binary":
        outcome_model = make_pipeline(
            MaxAbsScaler(),
            SGDClassifier(
                loss="log_loss",
                penalty="l2",
                alpha=1e-4,
                max_iter=2000,
                tol=1e-3,
                random_state=cfg.random_state,
            ),
        )
    else:
        outcome_model = make_pipeline(
            MaxAbsScaler(),
            SGDRegressor(
                loss="squared_error",
                penalty="l2",
                alpha=1e-4,
                max_iter=2000,
                tol=1e-3,
                random_state=cfg.random_state,
            ),
        )

    e_hat = np.empty((n,), dtype=np.float64)
    mu1_hat = np.empty((n,), dtype=np.float64)
    mu0_hat = np.empty((n,), dtype=np.float64)

    kf = KFold(n_splits=n_splits, shuffle=True, random_state=cfg.random_state)
    for train_idx, test_idx in kf.split(np.arange(n)):
        X_tr = X[train_idx]
        X_te = X[test_idx]
        T_tr = T[train_idx]
        y_tr = y[train_idx]

        # Propensity model e(X) = P(T=1|X)
        if int(T_tr.min()) == int(T_tr.max()):
            e_hat[test_idx] = float(T_tr.mean())
        else:
            m_prop = clone(prop_model)
            m_prop.fit(X_tr, T_tr)
            e_hat[test_idx] = _predict_proba_1(m_prop, X_te)

        # Outcome models μ1(X), μ0(X)
        treated_tr = T_tr == 1
        control_tr = ~treated_tr

        def _fallback_mean(mask: np.ndarray) -> float:
            if mask.any():
                return float(y_tr[mask].mean())
            return float(y_tr.mean())

        if treated_tr.sum() < 25 or (kind == "binary" and np.unique(y_tr[treated_tr]).size < 2):
            mu1_hat[test_idx] = _fallback_mean(treated_tr)
        else:
            m1 = clone(outcome_model)
            m1.fit(X_tr[treated_tr], y_tr[treated_tr])
            mu1_hat[test_idx] = (
                _predict_proba_1(m1, X_te) if kind == "binary" else np.asarray(m1.predict(X_te), dtype=np.float64)
            )

        if control_tr.sum() < 25 or (kind == "binary" and np.unique(y_tr[control_tr]).size < 2):
            mu0_hat[test_idx] = _fallback_mean(control_tr)
        else:
            m0 = clone(outcome_model)
            m0.fit(X_tr[control_tr], y_tr[control_tr])
            mu0_hat[test_idx] = (
                _predict_proba_1(m0, X_te) if kind == "binary" else np.asarray(m0.predict(X_te), dtype=np.float64)
            )

    # Guardrails: clip propensities away from 0/1 to avoid infinite weights.
    eps = float(cfg.clip_eps)
    e_hat = np.clip(e_hat, eps, 1.0 - eps)

    if kind == "binary":
        mu1_hat = np.clip(mu1_hat, 0.0, 1.0)
        mu0_hat = np.clip(mu0_hat, 0.0, 1.0)

    qs = np.quantile(e_hat, [0.01, 0.50, 0.99])

    if cfg.trim_low is not None and cfg.trim_high is not None:
        used = (e_hat >= float(cfg.trim_low)) & (e_hat <= float(cfg.trim_high))
    else:
        used = np.ones((n,), dtype=bool)

    n_used = int(used.sum())
    n_treated_used = int(T[used].sum())
    n_control_used = int(n_used - n_treated_used)

    # If trimming removes too much of the sample, the effect is not identifiable in this
    # feature space (positivity violation). Don't silently fall back to "use everything",
    # since clipped propensities can produce wildly unstable estimates.
    min_used = max(200, int(0.05 * n))
    if n_used < min_used or n_treated_used < 50 or n_control_used < 50:
        raise OverlapError(
            "Insufficient propensity overlap after trimming; effect is not reliably identifiable in this context.",
            n=n,
            n_used=n_used,
            n_treated_used=n_treated_used,
            n_control_used=n_control_used,
            trim_low=cfg.trim_low,
            trim_high=cfg.trim_high,
            e_min=float(e_hat.min()),
            e_p01=float(qs[0]),
            e_p50=float(qs[1]),
            e_p99=float(qs[2]),
            e_max=float(e_hat.max()),
            frac_trimmed=float(1.0 - (n_used / float(n))) if n else 1.0,
        )

    T_f = T.astype(np.float64, copy=False)
    y_f = y.astype(np.float64, copy=False)

    y1 = mu1_hat + T_f * (y_f - mu1_hat) / e_hat
    y0 = mu0_hat + (1.0 - T_f) * (y_f - mu0_hat) / (1.0 - e_hat)
    phi = (mu1_hat - mu0_hat) + T_f * (y_f - mu1_hat) / e_hat - (1.0 - T_f) * (y_f - mu0_hat) / (1.0 - e_hat)

    tau = float((y1[used] - y0[used]).mean())
    se = float(phi[used].std(ddof=1) / math.sqrt(n_used)) if n_used > 1 else float("nan")
    ci_low = tau - 1.96 * se if math.isfinite(se) else float("nan")
    ci_high = tau + 1.96 * se if math.isfinite(se) else float("nan")
    p_value = None
    if math.isfinite(se) and se > 0:
        p_value = _normal_two_sided_p_value(tau / se)

    estimate = AIPWEstimate(
        tau=tau,
        se=se,
        ci95_low=ci_low,
        ci95_high=ci_high,
        p_value=p_value,
        y1=float(y1[used].mean()),
        y0=float(y0[used].mean()),
        n=n,
        n_treated=n_treated,
        n_control=n_control,
        n_used=n_used,
        e_min=float(e_hat.min()),
        e_p01=float(qs[0]),
        e_p50=float(qs[1]),
        e_p99=float(qs[2]),
        e_max=float(e_hat.max()),
        frac_trimmed=float(1.0 - (n_used / float(n))),
    )
    return estimate, phi.astype(np.float64, copy=False), e_hat.astype(np.float64, copy=False), used
