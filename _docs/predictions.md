# Rating predictions: how it works

This page explains the models behind `edh.predictions` and why they are built this way. So far it covers **lineup
identity**: who a rating belongs to. The ratings themselves and their evaluation come next. To run it (history scope,
cache, command line, reading the lineups from Python), see the [rating predictions README](/edh/predictions/README.md).

## Contents

- [Lineup identity](#lineup-identity)
- [Rules](#rules)
- [Why causal](#why-causal)
- [Seasons](#seasons)
- [Limits](#limits)

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
out of 3. On the history (16.5k finished 3v3 matches since September 2024), the rules below find 4,380 lineups. 992 of
them took their core from a lineup under another name, most often from January to May, when each split's open
qualifiers run. In 70 of these cases the earlier lineup kept playing (53 clubs, 12 national teams, 5 college teams):
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

## Limits

- **The 2-of-3 core** treats a team that changes two players as new, even when the remaining player is its star.
  Player-level ratings (planned) address roster changes more finely.
- **120 days** is a measured window, not a fitted one: of the names that first appear with 2 players from another name,
  75% do so within 120 days of those players' last match together.
- **Kinds come from Liquipedia's presentation**: a club playing under a country name with a flag icon would count as
  national, and a college team playing outside collegiate events as a club in those events.
- **Missing rosters** are matched by name only, so a renamed team whose roster LPDB lacks is not linked to its
  predecessor until a roster is recorded.
