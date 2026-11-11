# League play scenarios: how it works

This page explains what `edh.scenarios` computes and why it is right: the probability model, the standings rules, the
three evaluation methods (exhaustive, winners-first, Monte Carlo), how results are aggregated and why parallel runs give
the same results. To run it (setup, `league.json`, command line, outputs, outlook card), see
the [league play scenarios README](/edh/scenarios/README.md).

> [!IMPORTANT]
> The shares are **shares of scenarios, not predictions**: every possible score of a remaining series is assumed
> equally likely, whoever plays it, and the engine has only been run on one competition's format so far. Read
> [Limits and planned evolution](#limits-and-planned-evolution) before treating a percentage as a team's chance.

## Contents

- [Pipeline](#pipeline)
- [Probability model](#probability-model)
- [Standings and tiebreakers](#standings-and-tiebreakers)
- [Three methods](#three-methods)
- [Exhaustive enumeration](#exhaustive-enumeration)
- [Monte Carlo sampling](#monte-carlo-sampling)
- [Winners-first (semi-exhaustive, exact)](#winners-first-semi-exhaustive-exact)
- [Aggregating the results](#aggregating-the-results)
- [Parallel execution](#parallel-execution)

## Pipeline

```
league.json ──┐
              ├─► evaluate(config, series, options) ─► ScenarioReport ─┬─► console table, outlook.csv / outlook.json
Liquipedia ───┤      prepare → plan → run chunks → merge               └─► outlook card (PNG)
or CSV ───────┘
```

1. **Format**: `_data/<league>/league.json` gives the series length, the ordered tiebreakers, the qualification slots
   and the team aliases ([fields](/edh/scenarios/README.md#leagues)).
2. **Results**: a source returns every series of the league, played or not (Liquipedia through `liquipydia`, or the
   saved `results.csv`). The engine never knows where they come from.
3. **Evaluation**: every remaining series is given its possible final scores; each scenario (one final score per
   remaining series) is ranked with the league's tiebreakers, and the rankings are summed into per-team shares.
4. **Output**: per team, the share of scenarios ending at each rank, qualifying for each slot, or stuck in a tie the
   tiebreakers can't settle; then the outlook card.

A series is `team_1`, `team_2`, the current score, whether it is finished and, when known, the goals of each game.
Series are split into **played** (finished) and **pending** (upcoming, or in progress at their current score).

## Probability model

Every **reachable final score** of a pending series is equally likely (`outcomes.reachable_scores`):

- A best of $2k-1$ games has $2k$ final scores: a Bo5 at 0-0 ends 3-0, 3-1, 3-2, 0-3, 1-3 or 2-3, each with
  probability $1/6$.
- A series in progress continues from its current score: at 2-0 in a Bo5 only 3-0, 3-1, 3-2 and 2-3 remain ($1/4$
  each), so the leader wins it $3/4$ of the time.
- A series already at its winning score keeps it, even if Liquipedia hasn't flagged it as finished yet.

Scenarios are therefore all equally likely, and an exact share is simply *scenarios where X happens / all scenarios*.
The number of scenarios is the product of the reachable score counts. With $m$ Bo5 series left, all at 0-0:

$$
N = 6^{m}, \qquad m = 15 \quad\Rightarrow\quad N = 6^{15} = 470{,}184{,}984{,}576.
$$

**Goals of simulated games are unknown.** A goal tiebreaker (`rounddiff`, `roundwins`) that would need them can't be
applied, so the tie is reported as *unresolved* rather than guessed (see below).

### Limits and planned evolution

**Equally likely scores is a strong assumption.** It is a neutral starting point, not a description of how series
are actually played:

- **Team strength is ignored.** The favourite and the bottom team each win a series at 0-0 with probability $1/2$.
- **Games are not coin flips either.** If each game were a fair coin flip, a Bo5 would end 3-0 with probability $1/8$
  and 3-1 or 3-2 with probability $3/16$ each (per winner). Equally likely scores give $1/6$ to each, so sweeps are
  slightly over-weighted and long series under-weighted, which matters for game-difference tiebreakers. Likewise, a
  team leading 2-0 wins with probability $3/4$ here, against $7/8$ with fair games.
- **Series are independent.** Form, momentum, roster changes or forfeits aren't modelled.

So a share reads as *the share of the remaining scenarios in which this happens*: a measure of what is still open and
of how much a team depends on other results, **not** a betting-style probability. One part doesn't depend on the
model: in an exact run, **0% and 100%** (the IN / OUT badges of the card) mean that no reachable outcome changes the
result. That holds under any model that gives every reachable score a non-zero probability.

**One format checked on real results so far.** The engine has been built and checked on Coupe de France Slash 2026:
6 teams in a double round robin (each pair meets twice, 30 Bo5 series over 6 days of 5 series, 10 series per team),
one series length for every match, Liquipedia's tiebreakers, no disqualification and no goals for simulated games. The
League Play of the RLCS 2027 Club Championship is configured from its official rulebook (seven regional leagues: single
round robins of 12 teams in EU / NA and 8 elsewhere, Bo5, ranked by match record, game difference, goal difference, then
initial seed), but its schedule isn't out yet, so it hasn't run on real results.

**Planned evolution** (in this order):

1. **Team strengths**: per-game win probabilities from ratings fitted on past results
   (the [rating predictions](/edh/predictions/README.md) project, which so far holds the match history and the lineups
   ratings will follow), turned into a
   distribution of final scores per series. The simulator stays the same: exact methods weight each scenario by its
   probability instead of counting it (winners-first already weighs winner combinations), and Monte Carlo draws each
   ending with its own probability.
2. **Series importance**: which remaining series swings each team's qualification share the most, computed from the
   simulator's own results.

## Standings and tiebreakers

`standings.rank` follows Liquipedia's `Module:Standings` resolution:

1. `tally` computes each team's record over the finished series: series won / lost, games won / lost, goals scored /
   conceded (and whether every goal is known).
2. Every team starts in **one tied group**.
3. The first tiebreaker splits the group by value, best value first. Each subgroup still tied moves on to the **next**
   tiebreaker; earlier tiebreakers are not applied again (no restart).
4. A group still tied after the last tiebreaker shares its ranks: a group of $k$ teams starting at rank $r$ covers
   ranks $r$ to $r+k-1$.

| Tiebreaker   | Value (higher is better)               |
|--------------|----------------------------------------|
| `matchscore` | (series won, −series lost)             |
| `matchdiff`  | series won − series lost               |
| `matchwins`  | series won                             |
| `gamescore`  | (games won, −games lost)               |
| `gamediff`   | games won − games lost                 |
| `gamewins`   | games won                              |
| `rounddiff`  | goals scored − goals conceded          |
| `roundwins`  | goals scored                           |
| `manual`     | organizer's points (never mini-league) |

The `ml.` prefix (**mini-league**) computes a tiebreaker over the series played **between the teams of the current
group only**, so its value changes as the group shrinks. `TieResolver` takes the full-league records and a function
returning mini-league records for any group, so the winners-first method can feed it records built without `Series`.

When a goal tiebreaker meets a team whose goals in its scope are unknown, the group stops splitting there (that
tiebreaker and the ones after it are skipped), and is counted as unresolved.

**Example** (Coupe de France Slash 2026: `matchscore`, `gamediff`, `ml.gamediff`, `ml.rounddiff`, `manual`): three
teams finishing 6-4 in series are first split by their game difference over the whole league; two of them still level
then compare their game difference in the two series they played against each other, then the goals of those two series.

## Three methods

| Method                                                                  | Exact | Applies when                                        |
|-------------------------------------------------------------------------|-------|-----------------------------------------------------|
| [Exhaustive](#exhaustive-enumeration)                                   | yes   | always, while the scenarios can be listed           |
| [Winners-first](#winners-first-semi-exhaustive-exact) (Semi-exhaustive) | yes   | the first tiebreaker depends on series winners only |
| [Monte Carlo](#monte-carlo-sampling)                                    | no    | always: a random sample of the scenarios            |

The two exact methods give the same shares; Monte Carlo estimates them within a margin. The default `auto` takes the
first that fits, under the limits given in the [README](/edh/scenarios/README.md#choosing-a-method).

## Exhaustive enumeration

For each pending series, every reachable ending is built once as a finished `Series` (goals unknown). Scenarios are
the Cartesian product (`itertools.product`) of those endings; each one is ranked together with the played series, and
the ranking is counted with weight 1. The played series are the same in every scenario, so their records are tallied
once (`standings.Standings`, per chunk): a scenario only adds its own series to a copy of them before the tiebreakers
run. Monte Carlo ranks its samples the same way.

Cost grows as $(2k)^m$ for $m$ remaining series: about 7 Bo5 series fit under the $10^6$ limit, since
$6^7 = 279{,}936$ and $6^8 = 1{,}679{,}616$.

## Monte Carlo sampling

When the scenarios are too many to list, Monte Carlo ranks a **random sample** of them and uses the shares observed
in the sample as **estimates** of the exact shares. This section explains why the estimates are unbiased, how precise
they are, and what they can't tell.

### What is estimated

Write $E_1, \dots, E_m$ for the reachable endings of the $m$ pending series. The scenario space is their Cartesian
product, with $N$ scenarios, all equally likely ([Probability model](#probability-model)):

$$
\Omega = E_1 \times \dots \times E_m, \qquad N = |\Omega| = \prod_{i=1}^{m} |E_i|.
$$

The exact share of an event $A \subseteq \Omega$ (say, "team X qualifies") is the probability of $A$ for a
scenario $\omega$ drawn uniformly from $\Omega$:

$$
p = \frac{|A|}{N} = \Pr_{\omega \sim \mathcal{U}(\Omega)}(\omega \in A).
$$

Exhaustive enumeration computes $p$ by visiting all $N$ scenarios. Monte Carlo instead draws $n$ scenarios at
random and counts how often $A$ happens.

### Drawing a scenario

A sample picks **one ending per pending series, independently and uniformly** among that series' reachable endings
(`engine._samples`: for each chunk of samples, one numpy `Generator.integers` call draws a table of indices, one row per
sample and one column per pending series, column $i$ bounded by $|E_i|$). Because the picks are independent, a given
scenario $\omega = (e_1, \dots, e_m)$ comes out with probability

$$
\Pr(\omega) = \prod_{i=1}^{m} \frac{1}{|E_i|} = \frac{1}{N},
$$

so each sample is a uniform draw from $\Omega$, without ever building $\Omega$. Samples are independent of each
other and drawn **with replacement**: the same scenario can come out twice, which is expected and doesn't bias anything.
Each sample is then ranked exactly like an exhaustive scenario.

### The estimator

For each sample $\omega_j$, let $X_j$ be its contribution to a share:

- $X_j = \mathbf{1}[\omega_j \in A] \in \lbrace 0, 1 \rbrace$ for *qualified*, *undecided* and *unresolved*;
- $X_j \in \lbrace 0, 1/k, 1 \rbrace$ for a **rank share**, since an unsettled tie of $k$ teams spreads the rank evenly.

The estimate is the sample mean:

$$
\hat{p} = \frac{1}{n} \sum_{j=1}^{n} X_j, \qquad \mathbb{E}[\hat{p}] = p, \qquad
\mathrm{Var}(\hat{p}) = \frac{\sigma^2}{n} \quad \text{with} \quad \sigma^2 = \mathrm{Var}(X_j) \le p(1-p).
$$

Since every $X_j$ has mean $p$, $\hat{p}$ is **unbiased**, whatever the league. The variance bound is an
equality for 0/1 contributions; a value in $[0, 1]$ with mean $p$ can't vary more, since $X_j^2 \le X_j$ gives
$\mathbb{E}[X_j^2] - p^2 \le p - p^2$.

### Precision

The **standard error** of a share, and the margin it gives at 95% confidence (central limit theorem, normal
approximation), are:

$$
\mathrm{SE}(\hat{p}) = \sqrt{\frac{p(1-p)}{n}}, \qquad
\Pr\bigl(\lvert \hat{p} - p \rvert \le 1.96 \cdot \mathrm{SE}(\hat{p})\bigr) \approx 0.95.
$$

$p(1-p)$ is largest at $p = 1/2$, so the worst case is $\mathrm{SE} = 1/(2\sqrt{n})$:

| Samples $n$       | Worst-case standard error | 95% margin (worst case) |
|-------------------|---------------------------|-------------------------|
| 10,000            | 0.50 pt                   | ± 0.98 pt               |
| 100,000 (default) | 0.16 pt                   | ± 0.31 pt               |
| 1,000,000         | 0.05 pt                   | ± 0.10 pt               |

Shares near 0% or 100% are more precise (a 2% share at 100,000 samples: ± 0.09 pt). Solving the worst case for $n$
gives the samples needed for a 95% margin $h$:

$$
n \ge \frac{1.96^2 \cdot \tfrac{1}{4}}{h^2} \approx \frac{0.96}{h^2}, \qquad h = 0.001 \quad\Rightarrow\quad n \approx 960{,}000.
$$

Consequences:

- **The error shrinks as $1/\sqrt{n}$**: halving the margin takes 4× the samples (and 4× the time).
- **Precision depends on $n$, not on $N$**: $10^9$ or $10^{20}$ scenarios left give the same margin for the
  same samples. This is why Monte Carlo is the fallback for large leagues, its cost being $n$ rankings.
- **Shares come with a margin**: a team at 50.2% against one at 49.9% after 100,000 samples is a tie for practical
  purposes. The **projected standings** can then swap between two seeds, since they compare shares.
- **Many shares, some outliers**: each of the team × rank shares has its own error. On CDF Slash 2026 (6 teams, 6
  ranks: 36 shares), about $36 \times 0.05 \approx 2$ of them are expected outside their 95% margin in any run.

Checked against an exact run: on CDF Slash 2026 with 15 series left, 1,000,000 samples matched all
$6^{15} = 470{,}184{,}984{,}576$ scenarios within 0.1 pt, as the table predicts.

### What a sample can't prove

Monte Carlo only sees what it draws. A share **estimated at 0% is not impossible**, only unseen: an outcome of
probability $p$ is missed by all $n$ samples with probability $(1-p)^n \approx e^{-np}$. When it is never seen,
the usual 95% upper bound on its probability (the "rule of three") solves $(1-p)^n = 0.05$:

$$
p_{\max} \approx \frac{-\ln 0.05}{n} \approx \frac{3}{n}, \qquad n = 100{,}000 \quad\Rightarrow\quad p_{\max} \approx 3 \times 10^{-5}.
$$

Likewise, 100% means "at least about 99.997%", not "certain". This is why the outlook card shows **IN / OUT badges for
exact runs only**:
a Monte Carlo run can't clinch or eliminate a team.

### Reproducibility and parallel runs

Draws use numpy's `Generator` (`numpy.random.default_rng`). Samples are drawn in chunks of 10,000 (`SAMPLE_BATCH`,
which bounds memory); each chunk gets its own seed **spawned** from the run's seed (`SeedSequence(seed).spawn`),
which numpy designs to give independent streams. Chunks are merged in order, so:

- with `--seed`, a run is reproducible, whatever `--workers` is;
- without it, each run draws fresh randomness, and two runs differ within the margins above.

The report states the number of samples (shown as "simulations" on the card, "scenarios" on exact runs).

Today every draw is uniform. Once per-team strengths exist ([planned evolution](#limits-and-planned-evolution)), each
series' ending will be drawn with its own probabilities; the estimator and its error stay the same.

## Winners-first (semi-exhaustive, exact)

Exact shares far beyond the exhaustive limit, when the **leading tiebreakers depend on series winners only**
(`matchscore`, `matchdiff`, `matchwins`, their `ml.` versions, `manual`; `winners.margin_free_prefix` counts them).
The idea: most of the ranking is settled by who wins each series; game margins only matter for teams level on those
leading tiebreakers, and only through the series those teams play.

### Stage 1: enumerate the winners

Each pending series $i$ has at most 2 outcomes (a winner $w$) instead of $2k$ scores. An outcome keeps its
**scores** $E_i^{w} \subseteq E_i$, the final scores giving that winner (equally likely among themselves), and its
**probability** is the share of the series' reachable scores it takes:

$$
q_i(w) = \frac{|E_i^{w}|}{|E_i|},
$$

i.e. $1/2$ at 0-0 and $3/4$ for the leader at 2-0 in a Bo5.

A winner combination $\mathbf{w} = (w_1, \dots, w_m)$ has probability $Q(\mathbf{w}) = \prod_i q_i(w_i)$. Its
records (series won / lost only) go through the leading tiebreakers (`TieResolver` on the margin-free prefix), which
split the teams into groups no margin can change. A team alone in its group gets its rank with probability
$Q(\mathbf{w})$.

15 Bo5 series left give $2^{15} = 32{,}768$ winner combinations instead of $6^{15} \approx 4.7 \times 10^{11}$
scenarios. The method is exact because it only regroups the exhaustive sum:

$$
\Pr(A) = \sum_{\mathbf{w}} Q(\mathbf{w}) \Pr(A \mid \mathbf{w}),
$$

where, given the winners, the scores of each series are uniform over $E_i^{w_i}$ and independent.

### Stage 2: resolve the tied groups over their margins

A group still tied is resolved with the remaining tiebreakers (from the first margin-based one on) over every margin
combination of **the series involving its teams only**. The other series' margins can't change how the group splits,
so they sum out.

This rests on linearity: a team's shares only depend on the group it lands in. Two tied groups sharing a series (one
team from each) are resolved separately, each summing that series' margins on its own, and the per-team totals are
still exact.

Within a tied group, the series split into:

- **Internal** series (both teams in the group): enumerated. Their margins form a *state*: per team, the games won /
  lost in them, then per ordered pair of teams, the games won / lost between the two (for `ml.` tiebreakers). Margin
  combinations reaching the same state are merged with their summed probability (`_spread`).
- **External** series (one team in the group): each only affects its own team's full-league game statistics, so they
  are summed into **one independent distribution per team** instead of being enumerated jointly.

States only keep the game statistics the remaining tiebreakers read (`_keep`): nothing, the difference only, the
games won only, or both games won and lost. A `gamediff`-only league, for instance, merges 3-1 and 4-2 into the same
"+2", so far fewer states remain.

### Comparing teams on a grid

A tiebreaker splits a group by value and orders the subgroups, so the result only depends on **how each pair of
teams compares** (better, equal, worse) on each statistic read, not on the values themselves. `_ComparisonGrid` uses
this:

1. The joint distribution of the external series is laid out once per (group, winners) as a numpy grid (outer
   product of the per-team distributions), with every pairwise difference of the compared statistics.
2. For each internal state, the state's totals shift those differences; their signs form a **comparison pattern**
   per cell, coded as a base-3 integer (up to 39 signs) so a 1-D `np.unique` groups the cells by pattern and
   `np.bincount` sums their probabilities.
3. Each distinct pattern is resolved once with `TieResolver`, using one representative cell's totals.

States sharing the same full-league part only differ in their mini-league records, so they are resolved together
(`_resolve_variants`).

### Caches

- **Per tied case**: the splits of a group depend on the group and on the winners of its own series only, so
  `(group, those winners)` is resolved once and reused by every winner combination reaching it.
- **Decision tree of splits** (`_cached_split`): a split depends on what the resolver reads, i.e. the full-league
  records (group, series records, comparison pattern), then the mini-league records of each subgroup it asks for,
  in a fixed order. The cache stores, at each node, either the final split or the next subgroup read; a split that
  reads no `ml.` record is shared by every internal state at once.

### Correctness and limits

The shares equal the exhaustive engine's (up to float rounding), which the tests check on random leagues for every kind
of tiebreaker chain.

The cost lies in **large ties on wins with many series between their teams**: their internal margins are still
enumerated: up to $3^{s}$ margin combinations for $s$ internal Bo5 series at 0-0 (3-0, 3-1 or 3-2 for the fixed winner),
before states merge. Run time therefore depends on the standings, not only on the number of series left, hence
the $2^{15}$ cap for `auto`. On the real CDF standings at 15 left, the exact shares over
all $6^{15} = 470{,}184{,}984{,}576$ scenarios matched a 1,000,000-sample Monte Carlo within 0.1 point.

## Aggregating the results

Every method feeds rank groups to an `_Accumulator`, each group with a weight (1 per exhaustive scenario or sample,
a probability for winners-first). For a group $G$ of $k$ teams covering ranks $r$ to $r+k-1$, with weight
$w$:

- **Rank shares**: each team gets $w/k$ on each rank of the group (an unsettled tie is spread evenly).
- **Qualified** (per slot): the whole group lies inside the slot, so every team in it qualifies whatever the
  tie's outcome.
- **Undecided** (per slot): the group straddles the slot's edge, so qualification depends on how the tie is settled.
- **Unresolved**: the team ended in a tie of several teams.
- **Slot share** (per slot, the share shown): the sum of the team's rank shares over the slot's ranks, so a tie
  straddling the slot counts for the part of it inside the slot, as in the rank shares. It lies between *qualified*
  and *qualified* + *undecided*, and equals *qualified* when no tie straddles the slot.

Totals are divided by the total weight $W$ at the end ($N$ scenarios, $n$ samples, or 1 for winners-first). Writing
$\mathcal{G}(t, \rho)$ for the weighted groups $(G, w)$ of the run that contain team $t$ and cover rank $\rho$
(i.e. $r_G \le \rho \le r_G + |G| - 1$, with $r_G$ the group's first rank):

$$
\mathrm{share}(t, \rho) = \frac{1}{W} \sum_{(G, w) \in \mathcal{G}(t, \rho)} \frac{w}{|G|}.
$$

A team's rank shares split the scenarios, and so do its qualified, undecided and **missed** totals for each slot
(missed: the group lies outside the slot). When only one part of such a split was ever counted, its share is exactly 1.
Summed in another order than $W$, winners-first probabilities can land a hair away from it (e.g. $0.9999999999999999$),
which would hide a certain outcome such as a guaranteed slot (the IN badge). Weights only grow, so a part never counted
is exactly 0.

### Projected standings

Teams are listed in **projected standings** order (`engine.project_ranks`): each rank goes to one team, taking
team / rank pairings from the most to the least likely and keeping a pairing when both the team and the rank are
still free. A team losing its most likely rank to a likelier team falls back to its next most likely free rank. Equal
shares go to the better rank, then to the team with the better expected rank. Shares are rounded to 12 decimals
(`PROJECTION_DIGITS`) first, so float noise between methods can't reorder ties.

## Parallel execution

`evaluate` cuts every run into **fixed chunks**, independent of the number of workers:

- **Exact methods**: by the outcomes of the first pending series (scores for exhaustive, winners for winners-first),
  fixing as many series as needed to get at least 64 chunks (`PARALLEL_CHUNKS`).
- **Monte Carlo**: one chunk per 10,000 samples, each with its own seed spawned from the run's seed
  (`SeedSequence(seed).spawn`).

Chunks run in a `ProcessPoolExecutor` and their accumulators are merged **in chunk order**, so a run gives the same
result whatever `--workers` is. Runs below 50,000 units of work (`PARALLEL_MIN_WORK`; a winner combination counts as
200 scenarios) stay in-process, since starting workers has a fixed cost.

For winners-first, the gain from more workers is bounded by the largest tied case, which stays serial, and by caches
that each worker builds on its own.
