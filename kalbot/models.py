"""Probability models.

Favorite-longshot bias: prediction-market prices are miscalibrated at the extremes
(longshots overpriced, favorites underpriced). We model the true probability as a
logit-sharpening of the market mid:

    logit(p_true) = beta * logit(p_mkt)     (beta > 1 => bias present)

beta is re-estimated weekly by a ridge-regularised (toward the prior) logistic
regression of settled outcomes on logit(price), fitted with Newton-Raphson.
"""
import math
import numpy as np

EPS = 1e-4


def logit(p):
    p = min(max(p, EPS), 1 - EPS)
    return math.log(p / (1 - p))


def sigmoid(x):
    if x >= 0:
        return 1 / (1 + math.exp(-x))
    z = math.exp(x)
    return z / (1 + z)


def debias(p_mkt: float, beta: float, alpha: float = 0.0) -> float:
    return sigmoid(alpha + beta * logit(p_mkt))


def fit_beta(prices, outcomes, prior_beta=1.1, ridge=20.0, iters=50):
    """MAP estimate of (alpha, beta) for P(y=1) = sigmoid(alpha + beta*logit(price)).
    Gaussian prior centred at (0, prior_beta) with precision `ridge`. Returns
    (alpha, beta, n)."""
    x = np.array([logit(p) for p in prices], dtype=float)
    y = np.array(outcomes, dtype=float)
    n = len(x)
    if n < 30:
        return 0.0, prior_beta, n
    X = np.column_stack([np.ones(n), x])
    w = np.array([0.0, prior_beta])
    prior = np.array([0.0, prior_beta])
    for _ in range(iters):
        z = X @ w
        mu = 1 / (1 + np.exp(-z))
        g = X.T @ (mu - y) + ridge * (w - prior)
        H = (X * (mu * (1 - mu))[:, None]).T @ X + ridge * np.eye(2)
        step = np.linalg.solve(H, g)
        w = w - step
        if np.max(np.abs(step)) < 1e-8:
            break
    return float(w[0]), float(w[1]), n


def brier(probs, outcomes):
    if not probs:
        return None
    return float(np.mean([(p - y) ** 2 for p, y in zip(probs, outcomes)]))


def norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def temp_bucket_prob(mu, sigma, strike_type, floor, cap):
    """P(integer daily max temp lands in a Kalshi bucket), with a continuity correction.
    'less' cap=49 -> T<=48 ; 'greater' floor=56 -> T>=57 ; 'between' 49..50 inclusive."""
    if strike_type == "less":
        return norm_cdf((cap - 0.5 - mu) / sigma)
    if strike_type == "greater":
        return 1 - norm_cdf((floor + 0.5 - mu) / sigma)
    if strike_type == "between":
        return norm_cdf((cap + 0.5 - mu) / sigma) - norm_cdf((floor - 0.5 - mu) / sigma)
    return None
