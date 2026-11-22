import marimo

__generated_with = "0.25.2"
app = marimo.App(width="medium", app_title="Rating models")


@app.cell
def _():
    import csv
    import json
    import math

    import marimo as mo
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    from edh import paths
    from edh.predictions import constants, evaluation, tuning

    return constants, csv, evaluation, go, json, make_subplots, math, mo, paths, tuning


@app.cell
def _(mo):
    mo.md(r"""
    # Rating models, compared

    Four rating models (uniform odds, Elo, Glicko-2, Bradley–Terry) predict the winner of every RLCS series, each
    following one of three **identities**: the team's **name**, its **lineup** (a core of players kept together,
    whatever the team is called) or its **players** (a side rated as the mean of its roster). Each model and
    identity is tuned on one season, then scored once on the next, every series predicted from the past only
    (how and why: `_docs/predictions.md`).

    This notebook reads a report saved by `python -m edh.predictions.evaluate` (`_data/predictions/<day>/`):
    nothing is tuned again here.
    """)
    return


@app.cell
def _(mo):
    mo.md(r"""
    ## The four models, in short

    Each model gives, before a match, the probability $p$ that a side wins **a game**; a series' probability follows
    from it (games taken as independent), and every model learns from the games each side won, not only the series
    winner. A side's rating is the mean of its members' (one name, one lineup, or three players), and a member's
    first match of a new season pulls its rating toward the average by a share `shrink`.

    - **Uniform odds**: $p = 1/2$ for every game, what the league scenarios assume today. The bar every model must
      beat: its series log loss is $\ln 2 \approx 0.693$.
    - **Elo**: one number per member. $p = 1 / (1 + 10^{(R_2 - R_1)/400})$: 400 points apart is 10 to 1 per game.
      After a series, each member gains `k` times (games won − games expected) and its opponents lose as much.
      `offset` is where a newcomer starts, relative to the average.
    - **Glicko-2**: a rating plus a **deviation** (how unsure it is) and a **volatility** (how erratic the side is). An
      unsure rating moves more after a surprise, and two unsure sides get a probability closer to even. `start_rd`
      is a newcomer's deviation, `tau` how fast volatility can change.
    - **Bradley–Terry**: strengths fitted again before each match day on **every** past game at once, not updated
      game by game: $p = 1 / (1 + e^{\theta_2 - \theta_1})$. Old games fade with a half-life of `half_life` days, and
      `l2` pulls each strength toward its prior (a new lineup's predecessor, else zero).

    The full rules and formulas: `_docs/predictions.md`, *Rating models*.
    """)
    return


@app.cell
def _(constants, mo, paths):
    reports = sorted(
        path.parent for path in (paths.DATA_DIR / constants.PREDICTIONS_FOLDER).glob(f'*/{constants.EVALUATION_JSON}')
    )
    picker = mo.ui.dropdown(
        options={folder.name: folder for folder in reports},
        value=reports[-1].name if reports else None,
        label='Report',
    )
    mo.output.replace(picker)
    return (picker,)


@app.cell
def _(constants, csv, evaluation, json, mo, picker):
    mo.stop(picker.value is None, mo.md('No evaluation saved yet: run `python -m edh.predictions.evaluate`.'))
    report = json.loads((picker.value / constants.EVALUATION_JSON).read_text(encoding='utf-8'))
    with (picker.value / constants.CALIBRATION_CSV).open(encoding='utf-8', newline='') as _file:
        bins = {}
        for _row in csv.DictReader(_file):
            _bin = evaluation.CalibrationBin(
                float(_row['low']),
                float(_row['high']),
                int(_row['matches']),
                float(_row['predicted']),
                float(_row['observed']),
            )
            bins.setdefault((_row['model'], _row['identity']), []).append(_bin)
    results = report['results']
    seasons = f'tuned on RLCS {report["tuning_season"]}, scored on RLCS {report["evaluation_season"]}'
    return bins, report, results, seasons


@app.cell
def _():
    # The outlook cards' graphite, and one hue per identity (checked for color-blind separation on that background)
    theme = {
        'background': '#0e0e10',
        'surface': '#1b1b1f',
        'line': '#2c2c32',
        'text': '#f2f2f4',
        'muted': '#8e8e97',
        'heat': '#1f7fbf',
        'bound': '#c9a646',
    }
    identity_colors = {'name': '#3987e5', 'lineup': '#d95926', 'players': '#199e70'}
    model_names = {'uniform': 'Uniform', 'elo': 'Elo', 'glicko2': 'Glicko-2', 'bradley_terry': 'Bradley–Terry'}
    layout = {
        'template': 'plotly_dark',
        'paper_bgcolor': theme['background'],
        'plot_bgcolor': theme['surface'],
        'font': {'family': 'JetBrains Mono, monospace', 'color': theme['text']},
        'margin': {'t': 60, 'l': 20, 'r': 20, 'b': 40},
    }
    return identity_colors, layout, model_names, theme


@app.cell
def _(mo, report, seasons):
    mo.md(f"""
    ## Which model predicts best?

    Every model and identity, {seasons}. The score is the **series log loss**, the mean of
    $-\\log$ of the probability given to the actual winner: lower is better, and even odds score
    $\\ln 2 \\approx 0.693$. Each column is a group of series: all of them, each Liquipedia tier, then series between
    two **experienced** lineups (10 matches or more before) or not. The color is the gain over even odds, on one
    scale for every column; hover a cell for its Brier score and accuracy.

    The team Liquipedia lists first won **{report['first_team_share']:.1%}** of these series: it is often the
    better seed. The ratings never use that order, so even odds stay the bar to beat.
    """)
    return


@app.cell
def _(constants, go, layout, math, mo, model_names, results, theme):
    _keys = [
        'all',
        *sorted(key for key in results[0]['evaluation'] if key.startswith('tier ')),
        'experienced',
        'inexperienced',
    ]
    _keys = [key for key in _keys if key in results[0]['evaluation']]
    _rated = sorted(
        (result for result in results if result['model'] != constants.MODEL_UNIFORM),
        key=lambda result: result['evaluation']['all']['log_loss'],
    )
    _rows = _rated + [result for result in results if result['model'] == constants.MODEL_UNIFORM][:1]
    _labels = [
        model_names[row['model']] + ('' if row['model'] == constants.MODEL_UNIFORM else f' · {row["identity"]}') for row in _rows
    ]
    _columns = [f'{key.capitalize()}<br>{results[0]["evaluation"][key]["matches"]:,} series' for key in _keys]
    _loss = [[row['evaluation'][key]['log_loss'] for key in _keys] for row in _rows]
    _hover = [
        [
            f'{label}<br>{key}: log loss {row["evaluation"][key]["log_loss"]:.4f}<br>'
            f'Brier {row["evaluation"][key]["brier"]:.4f} · accuracy {row["evaluation"][key]["accuracy"]:.1%}'
            for key in _keys
        ]
        for label, row in zip(_labels, _rows)
    ]
    comparison = go.Figure(
        go.Heatmap(
            z=[[math.log(2) - loss for loss in losses] for losses in _loss],
            x=_columns,
            y=_labels,
            text=[[f'{loss:.3f}' for loss in losses] for losses in _loss],
            texttemplate='%{text}',
            hovertext=_hover,
            hoverinfo='text',
            colorscale=[[0, theme['surface']], [1, theme['heat']]],
            zmin=0,
            xgap=3,
            ygap=3,
            colorbar={'title': 'gain over<br>even odds'},
        )
    )
    comparison.update_layout(**layout, height=80 + 46 * len(_rows), yaxis={'autorange': 'reversed'})
    mo.output.replace(comparison)
    return


@app.cell
def _(mo):
    mo.md(r"""
    ## Are the probabilities honest?

    A model is **calibrated** when its favorites given 70% win about 70% of the time. Series are grouped by the
    favorite's predicted chance (bins of 10 points, from 50%) and plotted against the share of favorites that won:
    on the dashed line, the model is calibrated; **above** it, favorites win more often than predicted (the model is
    too cautious); **below**, it is overconfident. Each series is read from its **favorite's side**: the ratings
    ignore which team Liquipedia lists first, so a 30% chance for the first team is a 70% chance for its opponent.
    A dot's size grows with its series (hover for the count). Uniform odds have no favorite and are left out.
    """)
    return


@app.cell
def _(bins, constants, evaluation, go, identity_colors, layout, make_subplots, math, mo, model_names, theme, tuning):
    _models = [
        model
        for model in tuning.MODELS
        if model != constants.MODEL_UNIFORM and any(task[0] == model for task in bins)
    ]
    _most = max(item.matches for items in bins.values() for item in items)
    calibration = make_subplots(
        rows=1,
        cols=len(_models),
        subplot_titles=[model_names[model] for model in _models],
        shared_yaxes=True,
        horizontal_spacing=0.04,
    )
    for _column, _model in enumerate(_models, start=1):
        calibration.add_trace(
            go.Scatter(
                x=[0.5, 1],
                y=[0.5, 1],
                mode='lines',
                line={'dash': 'dash', 'color': theme['line']},
                showlegend=False,
                hoverinfo='skip',
            ),
            row=1,
            col=_column,
        )
        for _identity, _color in identity_colors.items():
            _favored = evaluation.favorite_calibration(bins.get((_model, _identity), []))
            if not _favored:
                continue
            calibration.add_trace(
                go.Scatter(
                    x=[item.predicted for item in _favored],
                    y=[item.observed for item in _favored],
                    mode='lines+markers',
                    name=_identity,
                    legendgroup=_identity,
                    showlegend=_column == 1,
                    line={'color': _color, 'width': 2},
                    marker={'size': [8 + 10 * math.sqrt(item.matches / _most) for item in _favored]},
                    customdata=[[item.matches] for item in _favored],
                    hovertemplate=(
                        f'{model_names[_model]} · {_identity}<br>predicted %{{x:.1%}}, won %{{y:.1%}}'
                        '<br>%{customdata[0]:,} series<extra></extra>'
                    ),
                ),
                row=1,
                col=_column,
            )
    calibration.update_xaxes(tickformat='.0%', range=[0.48, 1.02], title_text="favorite's predicted chance")
    calibration.update_yaxes(tickformat='.0%', title_text='favorites that won', row=1, col=1)
    calibration.update_layout(**layout, height=520, legend={'orientation': 'h', 'y': -0.2})
    mo.output.replace(calibration)
    return


@app.cell
def _(mo):
    mo.md(r"""
    ## Did the tuning behave?

    Each model's hyperparameters are picked on the tuning season: a small grid first, then a bounded Nelder–Mead
    search from its best point. Below, each pick is placed between the bounds of its search (0 = lowest value
    searched, 1 = highest; on a log scale for those searched that way). A pick **on a bound** (gold) means the
    search wanted to go further: the bounds come from what each value means, so it rather points at something the
    model lacks. On players, Elo's starting offset sits at its lowest (a newcomer is far weaker than the average
    rated player) and Glicko-2's τ at its highest: both call for a prior on new players, not wider bounds.
    """)
    return


@app.cell
def _(constants, go, identity_colors, layout, math, mo, model_names, results, theme, tuning):
    def _position(name, value, bounds):
        low, high = bounds
        if name in constants.LOG_SCALE:
            return math.log(value / low) / math.log(high / low)
        return (value - low) / (high - low)

    picks = go.Figure()
    for _result in (result for result in results if tuning.MODELS[result['model']].bounds):
        _bounds = tuning.MODELS[_result['model']].bounds
        for _name, _value in _result['params'].items():
            _on_bound = _name in _result['at_bound']
            picks.add_trace(
                go.Scatter(
                    x=[_position(_name, _value, _bounds[_name])],
                    y=[f'{model_names[_result["model"]]} · {_name}'],
                    mode='markers',
                    marker={
                        'size': 14,
                        'color': theme['bound'] if _on_bound else identity_colors[_result['identity']],
                        'symbol': 'diamond' if _on_bound else 'circle',
                        'line': {'width': 1, 'color': theme['muted']},
                    },
                    name=_result['identity'],
                    legendgroup=_result['identity'],
                    showlegend=False,
                    hovertemplate=(
                        f'{model_names[_result["model"]]} · {_result["identity"]}<br>{_name} = {_value:g} '
                        f'(searched {_bounds[_name][0]:g} to {_bounds[_name][1]:g})'
                        f'{" · on a bound" if _on_bound else ""}<extra></extra>'
                    ),
                )
            )
    for _identity, _color in identity_colors.items():  # legend entries only
        picks.add_trace(
            go.Scatter(x=[None], y=[None], mode='markers', marker={'size': 12, 'color': _color}, name=_identity)
        )
    picks.update_xaxes(range=[-0.05, 1.05], tickvals=[0, 0.5, 1], ticktext=['lowest', 'middle', 'highest'])
    picks.update_layout(
        **layout,
        height=460,
        title='Picks within their search bounds (gold diamond: on a bound)',
        legend={'orientation': 'h', 'y': -0.15},
    )
    mo.output.replace(picks)
    return


@app.cell
def _(mo):
    mo.md(r"""
    Each tuning's runs, in order: the tuning log loss of the best run so far. A long flat end means the search had
    settled; a line still dropping when it stops would call for more runs.
    """)
    return


@app.cell
def _(go, identity_colors, layout, make_subplots, mo, model_names, results, tuning):
    _tuned = [
        model
        for model, space in tuning.MODELS.items()
        if space.bounds and any(result['model'] == model for result in results)
    ]
    convergence = make_subplots(
        rows=1,
        cols=len(_tuned),
        subplot_titles=[model_names[model] for model in _tuned],
        horizontal_spacing=0.06,
    )
    for _column, _model in enumerate(_tuned, start=1):
        for _result in (result for result in results if result['model'] == _model):
            _best, _line = float('inf'), []
            for _trial in _result['trials']:
                _best = min(_best, _trial['log_loss'])
                _line.append(_best)
            convergence.add_trace(
                go.Scatter(
                    x=list(range(1, len(_line) + 1)),
                    y=_line,
                    mode='lines',
                    name=_result['identity'],
                    legendgroup=_result['identity'],
                    showlegend=_column == 1,
                    line={'color': identity_colors[_result['identity']], 'width': 2, 'shape': 'hv'},
                    hovertemplate='run %{x}: best %{y:.4f}<extra></extra>',
                ),
                row=1,
                col=_column,
            )
    convergence.update_xaxes(title_text='run')
    convergence.update_yaxes(title_text='best tuning log loss', row=1, col=1)
    convergence.update_layout(**layout, height=420, legend={'orientation': 'h', 'y': -0.25})
    mo.output.replace(convergence)
    return


if __name__ == "__main__":
    app.run()
