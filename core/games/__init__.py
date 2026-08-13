"""The game layer — docs/game-spec.md.

A finite-horizon stochastic bargaining game with two-sided private resolve,
one per dyad, solved for Markov Perfect Bayesian Equilibrium by backward
induction and rolled forward to a DISTRIBUTION over event sequences.

Three properties hold across every module here:

- DETERMINISTIC. Solving a game is arithmetic. Nothing in this package calls
  an LLM, reads a clock, or touches the network, so a forecast frozen from it
  can be recomputed exactly (build-spec section 17).
- FINITE HORIZON, and that is load-bearing. An infinitely repeated game hands
  the folk theorem almost any path as an equilibrium — it would predict
  everything and therefore nothing. The horizon matches the forecast's.
- THE GAME PREDICTS EVENTS; IT NEVER PRICES THEM. Market movement comes from
  measured AFFECTED edges, downstream, so no price on any surface originates
  in a model.
"""
