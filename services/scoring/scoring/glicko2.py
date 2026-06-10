"""
Glicko-2 rating engine.

Ported directly from trader-elo-rater/scripts/glicko2.py — unchanged.
Pure math, no I/O, no external state.

Faithful implementation of Glickman's Glicko-2 (rating period = one
competition round). A "match" is a virtual pairwise comparison manufactured
from cohort composite scores: trader A beats trader B if A's Layer-1 composite
is higher than B's. Each trader ingests the full bag of pairwise results for
the round in a single batch update.

State per trader: rating (R), rating deviation (RD), volatility (sigma).
Defaults follow Glickman: R=1500, RD=350, sigma=0.06, system constant tau.
"""

import math

SCALE = 173.7178            # Glicko-2 internal scale factor
DEFAULT_RATING = 1500.0
DEFAULT_RD = 350.0
DEFAULT_VOL = 0.06
DEFAULT_TAU = 0.4           # lower tau = blowups treated as real signal
CONVERGENCE = 1e-6


class Rating:
    def __init__(self, rating=DEFAULT_RATING, rd=DEFAULT_RD, vol=DEFAULT_VOL):
        self.rating = float(rating)
        self.rd = float(rd)
        self.vol = float(vol)

    def to_dict(self) -> dict:
        return {
            "rating": round(self.rating, 2),
            "rd": round(self.rd, 2),
            "vol": round(self.vol, 6),
        }

    def __repr__(self):
        return f"Rating(r={self.rating:.1f}, rd={self.rd:.1f}, vol={self.vol:.4f})"


def _g(phi: float) -> float:
    return 1.0 / math.sqrt(1.0 + 3.0 * phi * phi / (math.pi * math.pi))


def _E(mu: float, mu_j: float, phi_j: float) -> float:
    return 1.0 / (1.0 + math.exp(-_g(phi_j) * (mu - mu_j)))


def rate(
    player: Rating,
    results: list[tuple[Rating, float]],
    tau: float = DEFAULT_TAU,
    rd_floor: float = 0.0,
) -> Rating:
    """
    Update one player's rating from a batch of results for the round.

    player   : Rating
    results  : list of (opponent_Rating, score) where score in {1.0, 0.5, 0.0}
    tau      : volatility constant
    rd_floor : minimum RD (190 projected, 50 confirmed)
    returns  : new Rating
    """
    if not results:
        phi = player.rd / SCALE
        phi_star = math.sqrt(phi * phi + player.vol * player.vol)
        new_rd = max(min(phi_star * SCALE, DEFAULT_RD), rd_floor)
        return Rating(player.rating, new_rd, player.vol)

    mu = (player.rating - DEFAULT_RATING) / SCALE
    phi = player.rd / SCALE

    v_inv = 0.0
    delta_sum = 0.0
    for opp, s in results:
        mu_j = (opp.rating - DEFAULT_RATING) / SCALE
        phi_j = opp.rd / SCALE
        g_j = _g(phi_j)
        e = _E(mu, mu_j, phi_j)
        v_inv += g_j * g_j * e * (1.0 - e)
        delta_sum += g_j * (s - e)

    v = 1.0 / v_inv
    delta = v * delta_sum

    # iterative volatility update (Illinois algorithm)
    a = math.log(player.vol**2)

    def f(x: float) -> float:
        ex = math.exp(x)
        num = ex * (delta * delta - phi * phi - v - ex)
        den = 2.0 * (phi * phi + v + ex) ** 2
        return num / den - (x - a) / (tau * tau)

    A = a
    if delta * delta > phi * phi + v:
        B = math.log(delta * delta - phi * phi - v)
    else:
        k = 1
        while f(a - k * tau) < 0:
            k += 1
        B = a - k * tau

    fA, fB = f(A), f(B)
    while abs(B - A) > CONVERGENCE:
        C = A + (A - B) * fA / (fB - fA)
        fC = f(C)
        if fC * fB <= 0:
            A, fA = B, fB
        else:
            fA /= 2.0
        B, fB = C, fC

    new_vol = math.exp(A / 2.0)
    phi_star = math.sqrt(phi * phi + new_vol * new_vol)
    new_phi = 1.0 / math.sqrt(1.0 / (phi_star * phi_star) + 1.0 / v)
    new_mu = mu + new_phi * new_phi * delta_sum

    return Rating(
        new_mu * SCALE + DEFAULT_RATING,
        max(new_phi * SCALE, rd_floor),
        new_vol,
    )


def league_rating(r: Rating) -> float:
    """Conservative displayed rating: R - 2*RD."""
    return r.rating - 2.0 * r.rd


def assign_tier(lr: float, tiers: list) -> str:
    for t in sorted(tiers, key=lambda x: -x.min):
        if lr >= t.min:
            return t.name
    return tiers[-1].name


def run_round(
    scores: dict[str, float],
    priors: dict[str, Rating] | None = None,
    tau: float = DEFAULT_TAU,
    rd_floor: float = 0.0,
) -> dict[str, Rating]:
    """
    Run one rating period over a cohort.

    scores   : {trader_id: composite_score}  (higher = better)
    priors   : {trader_id: Rating} from previous round (optional)
    rd_floor : state-dependent RD floor (190 projected / 50 confirmed)
    returns  : {trader_id: Rating} updated

    Virtual matches: every ordered pair (i, j). i beats j (1.0) if
    score_i > score_j, tie (0.5) if equal, loss (0.0) otherwise.
    """
    ids = list(scores.keys())
    priors = priors or {}
    current = {i: priors.get(i, Rating()) for i in ids}
    updated = {}
    for i in ids:
        results = []
        for j in ids:
            if i == j:
                continue
            if scores[i] > scores[j]:
                s = 1.0
            elif scores[i] == scores[j]:
                s = 0.5
            else:
                s = 0.0
            results.append((current[j], s))
        updated[i] = rate(current[i], results, tau=tau, rd_floor=rd_floor)
    return updated
