# Rating predictions: how it works

This page explains the models behind `edh.predictions` and why they are built this way: **lineup identity** (who a
rating belongs to), then the **rating models** and how they are evaluated. To run it (history scope, cache, command
lines, reading the lineups from Python, the evaluation report), see the
[rating predictions README](/edh/predictions/README.md).

## Contents

- [Lineup identity](#lineup-identity)
- [Rules](#rules)
- [Why causal](#why-causal)
- [Seasons](#seasons)
- [Lineup limits](#lineup-limits)
- [Rating models](#rating-models)
- [Walk-forward evaluation](#walk-forward-evaluation)
- [Model limits](#model-limits)

## Lineup identity

A rating has to follow what actually plays: three players. Team names are a poor proxy for that:

- **Renames and organization swaps** keep the players under a new name (Chimichanga → LeaveNoWitness, G2 Esports → NRG):
  a rating keyed by name would start such a team from scratch.
- **Roster changes** keep the name with other players: a name whose whole roster left keeps a rating that no longer
  describes anyone.
- **Concurrent squads** share players at the same time: national teams (FURIA's players as Brazil at the FIFAe World
  Cup) or
  college teams. A table mapping old names to new ones would merge them, and mix two teams' results into one rating.

So ratings follow **lineups**: a team name together with the rosters it plays with while keeping a **core** of 2 players
out of 3. On the history (21.3k finished 3v3 matches since September 2023), the rules below find 5,460 lineups. 1,205
of them took their core from a lineup under another name, most often from January to May, when each split's open
qualifiers run. In 86 of these cases the earlier lineup kept playing (70 clubs, 12 national teams, 4 college teams):
the concurrent squads a table of renames would have merged.

## Rules

The history is read in date order, and each side of a match is placed in a lineup with what was known before that
match:

1. **Same name, core kept**: a side whose roster has at least 2 players of its name's latest roster carries that lineup
   on, whatever the time since its last match (an off-season included). The roster is updated, so changes chain
   along: $\lbrace a, b, c\rbrace \to \lbrace a, b, d\rbrace \to \lbrace a, d, e\rbrace$ stays one lineup. A substitute
   listed as a fourth player counts like the others.
2. **Otherwise, a new lineup**. It records as its **predecessor** the lineup it shares a core with, among those that
   played within the last **120 days**. With several candidates: club lineups first, then the most players in common,
   then the one that played last. When ratings come, a new lineup starts from its predecessor's rating instead of the
   default one.
3. **Kinds**: a side is **national** when its team icon is a flag, **college** in a collegiate event, a **club**
   otherwise. A club never takes a national or college lineup as its predecessor: such a squad is a side project of its
   players, its results say little about a club they form later. A national or college lineup may start from a club.
4. **No roster**: when fewer than 2 players are recorded (about 4% of sides), a side joins its name's lineup if that
   one played within the last 120 days (and keeps it recent), else a new lineup without players. The next roster under
   that name takes it over, unless that roster brings back the core of the name's last lineup with players, which then
   carries on as in rule 1.

The predecessor itself goes on unchanged, which settles renames and concurrent squads with the same rule:

| Case                           | What happens                                                                       |
|--------------------------------|------------------------------------------------------------------------------------|
| Rename (G2 Esports → NRG)      | NRG starts from G2's lineup; G2 Esports never plays again                          |
| National team (FURIA → Brazil) | Brazil starts from FURIA's lineup; both go on, each with its own results           |
| Core splits                    | The players who leave with a core form a new lineup from it; the others start anew |
| Same name, new players         | A new lineup without predecessor (unless the newcomers bring a core of their own)  |

## Why causal

A rule that looks at the whole history could tell a rename from a concurrent squad: if the old name plays again, it was
not a rename. But a prediction made before a match cannot see that future, and an evaluation run on identities it could
not have known would look better than the real thing. Placing each side with only the past keeps the walk-forward
evaluation honest: every identity is one a prediction could have used on that day.

The predecessor link is what makes this possible without guessing. At its first match, a new name sharing a core is
ambiguous; starting it from the core's lineup is the right prior in both cases (the same players), and keeping the two
lineups apart afterwards is right in both cases too (a renamed team's old name simply stops).

## Seasons

A season starts the day after the previous World Championship final, so off-season events, where the new rosters form,
count in the coming season (the dates: `SEASON_STARTS` in [`constants.py`](/edh/predictions/constants.py)). Seasons
don't break lineups: they mark where a rating carried over should be pulled back toward the mean, since a team's level
drifts more over an off-season than between two weeks of the same split.

## Lineup limits

- **The 2-of-3 core** treats a team that changes two players as new, even when the remaining player is its star.
  Player-level ratings (planned) address roster changes more finely.
- **120 days** is a measured window, not a fitted one: of the names that first appear with 2 players from another name,
  two thirds do so within 120 days of those players' last match together.
- **Kinds come from Liquipedia's presentation**: a club playing under a country name with a flag icon would count as
  national, and a college team playing outside collegiate events as a club in those events.
- **Missing rosters** are matched by name only, so a renamed team whose roster LPDB lacks is not linked to its
  predecessor until a roster is recorded.

## Rating models

A rating model gives, before a match, the probability $p$ that the first team wins **a game**. That is what the
league play simulator needs: with games taken as independent, a team needing $w$ more wins takes the series while the
other wins $j$ more games (fewer than it needs) with probability

$$
\binom{w-1+j}{j} p^{w} (1-p)^{j},
$$

its last win ending the series. In a best of 5 at 0-0, $p = 0.6$ gives the series with probability $0.683$. The
models learn from games too: a 3-0 says more than a 3-2, and every series score says how many games each team won,
even when the goals of each game are not recorded.

### Members and identities

A side of a match is a set of **members**, and its rating is the mean of its members' ratings; an update moves every
member. Three identities are compared, with the same models:

| Identity | A side's members                                          | A new member starts from               |
|----------|-----------------------------------------------------------|----------------------------------------|
| Name     | its team name                                             | the default rating                     |
| Lineup   | its [lineup](#lineup-identity)                            | its predecessor's rating, else default |
| Players  | its players (its lineup's latest roster if none recorded) | the default rating                     |

A member's first match of a new season pulls its rating toward the mean by a share $s$ (tuned), since a team's level
drifts more over an off-season than within a split. A new lineup whose predecessor last played in an earlier season is
pulled too, as its predecessor would have been: renaming over the off-season doesn't spare a team the shrinkage.

### The four models

- **Uniform** (the baseline): $p = 1/2$ for every game, the probability model of the league play scenarios today.
- **Elo**: ratings $R$, $p = 1 / (1 + 10^{(R_2 - R_1)/400})$. After a series with $g_1$ games won and $g_2$ lost, each
  member of the first side gains $K (g_1 - (g_1 + g_2) p)$ and each member of the second side loses as much: games won
  minus games expected. Tuned: $K$, $s$, and the starting rating of a new member relative to the mean.
- **Glicko-2** (Glickman): each member carries a rating $\mu$, a deviation $\phi$ (how unsure the rating is) and a
  volatility $\sigma$. A series is one rating period, each game an outcome against the other side (its mean $\mu$ and
  root mean square $\phi$); a member's expected scores come from its own side's mean, so all members of a side get the
  same surprise, the less certain moving more. $p$ combines both deviations:
  $p = 1 / (1 + e^{-g(\phi) (\mu_1 - \mu_2)})$ with $\phi^2 = \phi_1^2 + \phi_2^2$ and
  $g(\phi) = 1 / \sqrt{1 + 3 \phi^2 / \pi^2}$. Tuned: $\tau$ (how fast volatility moves), the starting deviation, $s$.
- **Bradley–Terry**: strengths $\theta$ fitted before each match day on **all** past games,
  $p = \sigma(\theta_1 - \theta_2)$ with $\sigma$ the logistic function. The fit maximizes

  $$
  \sum_{\text{series}} w \left( g_1 \log p + g_2 \log (1 - p) \right) - \frac{\lambda}{2} \sum_{m} (\theta_m - \pi_m)^2,
  $$

  where $w = 2^{-\text{age} / h}$ fades old games with a half-life $h$ and the prior $\pi_m$ is the predecessor's
  strength when the member appeared (0 otherwise). It is refitted by majorize-minimize steps from the previous day's
  strengths: a side's gap is a weighted sum of strengths whose absolute weights add up to 2, which bounds the
  curvature per member, so every step increases the objective. Tuned: $h$ and $\lambda$; no season shrinkage, the
  half-life already lets old results fade.

## Walk-forward evaluation

Matches are taken in date order. Each one is predicted with the ratings built from the matches **before** it, then
used to update them, so every prediction is one that could have been made that day (Bradley–Terry is refitted once per
day, on the previous days' games only). Walkovers are neither predicted nor learned from.

- **Warm-up**: **RLCS 2024** (the history starts in September 2023) only builds the ratings, so that the tuning season
  starts with rated teams and crosses a season boundary, which the season shrinkage needs to be tuned at all.
- **Tuning**: every combination of a small grid of hyperparameters is run over the whole history, and the one with the
  lowest series log loss on **RLCS 2025** is kept.
- **Evaluation**: **RLCS 2026** is scored once, with the hyperparameters picked on 2025. The ratings keep learning
  through 2026 (each match still predicted before it is learned from).

For each series, with $y = 1$ when the first team won and $\hat{p}$ its predicted series probability:

| Score         | Definition                                                         | Uniform               |
|---------------|--------------------------------------------------------------------|-----------------------|
| Log loss      | mean of $-(y \log \hat{p} + (1 - y) \log (1 - \hat{p}))$           | $\ln 2 \approx 0.693$ |
| Brier score   | mean of $(\hat{p} - y)^2$                                          | $0.25$                |
| Accuracy      | share of series whose winner was given more than $1/2$             | $0.5$                 |
| Game log loss | the log loss of every game, from $p$                               | $\ln 2$               |

Scores are also split by tier and by experience (both lineups with at least 10 matches before, or not), and series
predictions are grouped into 10 bins of predicted probability to check calibration: in a calibrated model, the series
predicted around 70% are won about 70% of the time.

## Model limits

- **Games are taken as independent** within a series, with the same $p$: momentum and adaptation are ignored.
- **Order of the opponents**: the first team listed by Liquipedia wins most series (it is often the better seed; the
  evaluation reports the share, `first_team_share`, about two thirds of RLCS 2026). The ratings don't use the order,
  but a constant "the first team wins that share" already beats uniform: the baseline to beat is uniform, as in the
  simulator, not the best constant.
- **Small grids**: each hyperparameter takes a few values only; a value picked at the edge of its grid may be improved
  by a finer search.
- **No LAN / online or region effect**, and every tier weighs the same.
