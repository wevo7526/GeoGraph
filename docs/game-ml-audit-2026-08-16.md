# The game among allies and adversaries — audit, and the architecture it implies

*2026-08-16. Prompted by a reader's observation that the solved games "among
allies don't make sense at all, like the US with AUS, JPN". They don't, and the
reason is measurable.*

## 1. What the surface actually shows

Every solved dyad in the three production regions, ranked by the metric the
game-theory page ranks by (`sharp_departure_probability`):

| region | top of "sharpest friction" | where the declared rivalry sits |
|---|---|---|
| china | Russia–China (**alliance**) 0.326, US–South Korea (**alliance**) 0.317, US–Philippines (**alliance**) 0.313 | US–China (**rivalry**) **last of twelve**, 0.226 |
| eurasia | US–Turkey, US–Germany, UK–Germany — all alliances | US–Russia (**rivalry**) 9th of 12 |
| mena | US–Lebanon, Iran–Iraq, US–Turkey | — |

Mean over 36 solved dyads: **allies 0.2332, rivalries 0.2338.** The model does
not distinguish them at all.

## 2. Why — three mechanisms, all measured

**a. One kernel for a whole region.** `transition.kernel` counts
`P(next band | band, action_a, action_b)` over every dyad in the pack. US–Japan
and North Korea–South Korea are solved over the *same* transition table. At
band 2 the counted kernel returns an expected next band of **0.60 for every
pair on the board** — identical, by construction.

**b. The only per-dyad inputs are four scalars**, and they barely vary:

| | US–Japan (ally) | NK–SK (rivalry) |
|---|---|---|
| opening band | 1 | 1 |
| beliefs (a, b) | 0.36, 0.90 | 0.36, 0.90 |
| ML tilt η | 0.4815 | 0.500 |
| coercive share of its record | 9.4% | **29.7%** |

The one fact that separates them — how coercive their record actually is —
**is not an input to the game.**

**c. The ML tilt is saturated.** `bridge.eta_from_trajectory` is capped at
`TILT_SCALE = 0.5`; **5 of 12** china dyads sit exactly at the bound and the
mean is 0.345. A parameter pinned at its limit for the busiest pairs carries no
information about them.

## 3. Where ML is employed today

Two places, both real, neither touching the game's dynamics:

1. **`models/intensity.json`** — a within-dyad-gated ridge on intensity
   deviations. Produces the `model` Forecast mode, and a per-dyad trajectory
   that becomes the single scalar η above.
2. **`models/game-<region>.json`** — five payoff parameters fitted per REGION by
   indirect inference (`games/estimate.py`).

Everything else is counted or curated: the kernel, the base rates, Head B's
escalation coding, the transmission engine.

## 4. The architecture the evidence supports

### Model A — the transition model (gives the game dyad-specific dynamics)

```
P(next band) = softmax( log P_counted(next | band, a, b)  +  X·W )
                        └── offset: the counted evidence ──┘   └ learned ┘
```

* **Offset** — the counted kernel, per-dyad counts shrunk toward the region
  pool (Dirichlet, k≈80). `W = 0` reproduces today's behaviour exactly, so the
  model cannot be worse than the counts by construction; it can only add what
  counting does not know.
* **Features (X)** — `volume` (log events over 4 quarters), `coercive` (material
  conflict share), `volatility`, `gap`, plus band×action structure and two
  interactions.
* **Fit** — multinomial logistic, L-BFGS, L2, standardised features. ~200
  parameters, all named and inspectable. No new dependency.

Held out on a time split (75/25), against the kernel that ships today:

| region | pooled log-loss | within-dyad log-loss | within-dyad ρ |
|---|---|---|---|
| mena | 1.4834 → **1.3539** | 1.4388 → **1.3208** | 0.116 → **0.124** |
| china | 1.3767 → **1.2387** | 1.3781 → **1.2426** | 0.090 → **0.133** |
| eurasia | 1.3863 → **1.2455** | 1.2627 → **1.1581** | 0.050 → **0.102** |

It also **travels**: fitted on two regions and applied to the third — a region
it never saw — it beats that region's own counted kernel in all three
directions (mena 1.4834→1.4015, china 1.3767→1.1717, eurasia 1.3863→1.2509).
Calibration is honest: says 0.28 → right 0.22; says 0.79 → right 0.77;
accuracy 0.541 against the counted kernel's 0.480.

### Model B — the ranking model (gives the surface a question it can answer)

The band is a departure from each pair's **own** scale, so no amount of kernel
work makes "expected band" comparable across pairs — a quiet ally at the top of
its own range will always outrank a war. If pairs are ranked against each other
the target has to be measured on one scale for everyone:

> **P(this pair's next quarter carries material coercion)** — coercive share
> ≥ 15% on a non-trivial volume.

| region | base rate | log-loss vs base | AUC |
|---|---|---|---|
| mena | 0.230 | 0.796 → **0.473** | **0.914** |
| china | 0.096 | 0.673 → **0.645** | **0.915** |
| eurasia | 0.109 | 0.769 → **0.661** | **0.857** |

## 5. The uncomfortable finding

**The graph's curated relations do not predict transitions.** Leave-one-out over
all three regions: dropping `ally`, `rival`, `bloc`, `proxy` or the CINC ratio
changes held-out log-loss by ≤0.001 — several of them *improve* it. Adding
standing to the absolute model moves AUC by 0.000–0.001.

What does the work is the pair's own record in the wire: `volume` (dropping it
costs +0.045 and takes ρ from 0.108 to 0.091), then `coercive` and
`volatility`. And `level` — mean intensity — is the trap `core/models/
features.py` already documents: it improves pooled loss (+0.008) while
*hurting* within-dyad ordering, so it is excluded.

That is not an argument against the graph. The wire's events, actors, dyads and
escalation coding **are** the graph, and the curated layer still earns its place
by *describing* a pair (the standing chip), gating admissibility by regime, and
supplying capability and network structure. But as predictive features for what
happens next quarter, the declared edges are dominated by observed behaviour,
and the honest thing is to say so rather than to ship features that do nothing.

## 6. What is not yet tested

* `NetworkMetric` centrality and brokerage — absent from the dev graph, present
  in production; the most promising untested graph feature.
* AFFECTED-derived market materiality per dyad (how hard markets react to this
  pair) — a genuinely graph-native signal.
* Per-dyad random effects on top of the residual.
* Horizons beyond one quarter.
