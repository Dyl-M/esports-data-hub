import marimo

__generated_with = "0.25.2"
app = marimo.App(width="medium", app_title="Kick-Off 2027 forecasts")


@app.cell
def _():
    import datetime as dt
    import json

    import marimo as mo
    import plotly.graph_objects as go

    from edh import paths
    from edh.predictions import constants, history, lineups, series

    return constants, dt, go, history, json, lineups, mo, paths, series


@app.cell
def _(mo):
    mo.md(r"""
    # RLCS 2027 Kick-Off: the frozen forecasts

    Before the Kick-Off started (London, December 9 to 13), every pair of its 24 teams was forecast with each rating
    model and identity, and the file was frozen: `_data/predictions/kickoff_2027/frozen.json`. Nothing here forecasts
    again; this notebook reads that file, so what it shows is what the models said beforehand.

    The Kick-Off is a 24-team double elimination. Upper Bracket rounds 1 and 2 are best of 5; the rest of the bracket
    is assumed best of 7 here. The forecasts give the chance to win **a game**; a series' chance follows from it, games
    taken as independent. The bracket's seeds are not known yet, so who meets whom can't be simulated: the ranking
    below rates each team against the whole field instead.
    """)
    return


@app.cell
def _():
    # The outlook cards' graphite (same as the rating models notebook)
    theme = {
        'background': '#0e0e10',
        'surface': '#1b1b1f',
        'line': '#2c2c32',
        'text': '#f2f2f4',
        'muted': '#8e8e97',
        'heat': '#1f7fbf',
        'cold': '#c9a646',
    }
    model_names = {'uniform': 'Uniform', 'elo': 'Elo', 'glicko2': 'Glicko-2', 'bradley_terry': 'Bradley–Terry'}
    layout = {
        'template': 'plotly_dark',
        'paper_bgcolor': theme['background'],
        'plot_bgcolor': theme['surface'],
        'font': {'family': 'JetBrains Mono, monospace', 'color': theme['text']},
        'margin': {'t': 60, 'l': 20, 'r': 20, 'b': 40},
    }
    return layout, model_names, theme


@app.cell
def _(constants, json, mo, paths):
    _path = paths.DATA_DIR / constants.PREDICTIONS_FOLDER / 'kickoff_2027' / constants.FROZEN_JSON
    mo.stop(not _path.exists(), mo.md(f'No frozen file at `{_path}`: run `python -m _scripts.freeze_kickoff_2027`.'))
    frozen = json.loads(_path.read_text(encoding='utf-8'))
    tasks = [(pick['model'], pick['identity']) for pick in frozen['picks'] if pick['model'] != 'uniform']
    mo.md(
        f"Frozen at **{frozen['frozen_at']}**, history through **{frozen['history_through']}**, forecast for "
        f"**{frozen['forecast_day']}**: {len(frozen['teams'])} teams, {len(frozen['pairs'])} pairs."
    )
    return frozen, tasks


@app.cell
def _(dt, frozen, history, lineups):
    # Each roster's RLCS 2027 series before the freeze (at least two of its players together): the season's evidence
    _cutoff = dt.datetime.fromisoformat(frozen['frozen_at'])
    _rosters = {team['team']: set(team['roster']) for team in frozen['teams']}
    season_series = dict.fromkeys(_rosters, 0)
    for _match in history.read_history():
        if _match.played_at >= _cutoff or lineups.season_of(_match.played_at.date()) != 2027:
            continue
        for _opponent in _match.opponents:
            _pages = {player.page for player in _opponent.players}
            for _team, _roster in _rosters.items():
                if len(_roster & _pages) >= 2:
                    season_series[_team] += 1
    return (season_series,)


@app.cell
def _(mo, model_names, tasks):
    task = mo.ui.dropdown(
        options={f'{model_names[model]} · {identity}': (model, identity) for model, identity in tasks},
        value='Elo · players',
        label='Model',
    )
    best_of = mo.ui.radio(options={'Best of 5': 5, 'Best of 7': 7}, value='Best of 5', label='Series', inline=True)
    mo.output.replace(mo.hstack([task, best_of], justify='start', gap=2))
    return best_of, task


@app.cell
def _(best_of, frozen, series, task):
    def series_chances(model, identity, length):
        """Each team's series chance against every other team, and its per-game chances, for a model and identity."""
        wins_needed = length // 2 + 1
        by_series = {team['team']: {} for team in frozen['teams']}
        by_game = {team['team']: {} for team in frozen['teams']}
        for pair in frozen['pairs']:
            p = pair['game_probability'][model][identity]
            first, second = pair['first'], pair['second']
            chance = series.series_probability(p, wins_needed)
            by_series[first][second], by_series[second][first] = chance, 1 - chance
            by_game[first][second], by_game[second][first] = p, 1 - p
        return by_series, by_game

    chances, games = series_chances(*task.value, best_of.value)
    ranking = sorted(chances, key=lambda team: -sum(chances[team].values()))
    never_rated = {team['team']: team['never_rated'][task.value[1]] for team in frozen['teams']}
    return chances, games, never_rated, ranking, series_chances


@app.cell
def _(best_of, mo, task):
    mo.md(f"""
    ## Power ranking

    Each team's mean chance to win a **best of {best_of.value}** against the other 23, with
    {task.selected_key}. A team at 50% is as strong as the average Kick-Off team; the field is strong, so the order
    matters more than the level. Hover a bar for the per-game chance. Gold bars are **cold starts**: the identity
    never rated the team or some of its players (a new name, a newcomer to the history), so its rating leans on the
    starting value more than on results. A **▲** marks a team that played RLCS 2027 series before the freeze (the
    number in brackets): the four Open winners, the only teams with results this season.

    **Read the Open winners with care.** The 20 invited teams haven't played since the 2026 Worlds, so nothing this
    season connects them to the Opens' regional fields, and the models count each Open win at face value. With Elo on
    players and best of 5, the same rosters rated just before the Opens (September 21) ranked Geekay Esports 14th
    (49%), NOT 16th (44%), PCS Stride 21st (32%) and M80 11th (54%); their Open runs lifted them to 3rd, 6th, 11th
    and 7th, while the invited teams kept their order. Whether that lift is earned or inflated is what the Kick-Off's
    results will tell: the scoring compares the Open winners' forecasts with the invited teams'.
    """)
    return


@app.cell
def _(
    chances,
    games,
    go,
    layout,
    mo,
    never_rated,
    ranking,
    season_series,
    theme,
):
    _mean = {team: sum(chances[team].values()) / len(chances[team]) for team in ranking}
    _game = {team: sum(games[team].values()) / len(games[team]) for team in ranking}
    power = go.Figure(
        go.Bar(
            x=[_mean[team] for team in ranking],
            y=[
                f'{rank}. {team}' + (f' ▲ ({season_series[team]})' if season_series[team] else '')
                for rank, team in enumerate(ranking, start=1)
            ],
            orientation='h',
            marker={'color': [theme['cold'] if never_rated[team] else theme['heat'] for team in ranking]},
            text=[f'{_mean[team]:.0%}' for team in ranking],
            textposition='outside',
            customdata=[
                [_game[team], ', '.join(never_rated[team]) or 'none', season_series[team]] for team in ranking
            ],
            hovertemplate=(
                '%{y}<br>series: %{x:.1%} · game: %{customdata[0]:.1%}<br>never rated: %{customdata[1]}'
                '<br>RLCS 2027 series before the freeze: %{customdata[2]}<extra></extra>'
            ),
        )
    )
    power.add_vline(x=0.5, line={'dash': 'dash', 'color': theme['muted']})
    power.update_xaxes(range=[0, 1], tickformat='.0%', title_text='mean series chance against the field')
    power.update_layout(**layout, height=760, yaxis={'autorange': 'reversed'})
    mo.output.replace(power)
    return


@app.cell
def _(mo):
    mo.md(r"""
    ## Do the models agree?

    The rank of each team under every model and identity (1 = strongest), teams in the order of the ranking above.
    A row of similar numbers is a team every model reads the same way; a row that swings is one whose strength
    depends on how it is identified, usually a roster that changed (its name keeps the old results, its players
    bring their own).
    """)
    return


@app.cell
def _(
    best_of,
    go,
    layout,
    mo,
    model_names,
    ranking,
    series_chances,
    tasks,
    theme,
):
    _columns, _ranks = [], {team: [] for team in ranking}
    for _model, _identity in tasks:
        _chances, _ = series_chances(_model, _identity, best_of.value)
        _order = sorted(_chances, key=lambda team: -sum(_chances[team].values()))
        _columns.append(f'{model_names[_model]}<br>{_identity}')
        for _team in ranking:
            _ranks[_team].append(_order.index(_team) + 1)
    agreement = go.Figure(
        go.Heatmap(
            z=[_ranks[team] for team in ranking],
            x=_columns,
            y=ranking,
            text=[[str(rank) for rank in _ranks[team]] for team in ranking],
            texttemplate='%{text}',
            colorscale=[[0, theme['heat']], [1, theme['surface']]],
            showscale=False,
            xgap=2,
            ygap=2,
            hovertemplate='%{y} · %{x}: rank %{z}<extra></extra>',
        )
    )
    agreement.update_layout(**layout, height=760, yaxis={'autorange': 'reversed'})
    mo.output.replace(agreement)
    return


@app.cell
def _(best_of, mo):
    mo.md(f"""
    ## Head to head

    The chance that the team of the row wins a best of {best_of.value} against the team of the column, teams in the
    ranking's order: blue above 50%, gold below, the darker the closer to even. Hover a cell for both teams and the
    chance.
    """)
    return


@app.cell
def _(chances, go, layout, mo, ranking, theme):
    head_to_head = go.Figure(
        go.Heatmap(
            z=[[chances[row].get(column) for column in ranking] for row in ranking],
            x=ranking,
            y=ranking,
            colorscale=[[0, theme['cold']], [0.5, theme['surface']], [1, theme['heat']]],
            zmin=0,
            zmax=1,
            xgap=1,
            ygap=1,
            colorbar={'title': 'series<br>chance', 'tickformat': '.0%'},
            hovertemplate='%{y} vs %{x}: %{z:.1%}<extra></extra>',
        )
    )
    head_to_head.update_xaxes(tickangle=-45)
    head_to_head.update_layout(**layout, height=820, yaxis={'autorange': 'reversed'})
    mo.output.replace(head_to_head)
    return


if __name__ == "__main__":
    app.run()
