"""Generate the status page.

Deliberately a single static HTML file with no JavaScript and no build step.
The pipeline writes it at the end of every run and GitHub Pages serves it from
docs/. Anyone can see whether the thing is alive without credentials.
"""

import datetime as dt
import html
import pathlib

OUTPUT = pathlib.Path(__file__).resolve().parent.parent / "docs" / "index.html"

CSS = """
:root { color-scheme: light dark; }
body { font: 15px/1.55 ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
       margin: 0; padding: 2.5rem 1.25rem; background: #fbfbfa; color: #1c1b19; }
main { max-width: 62rem; margin: 0 auto; }
h1 { font-size: 1.5rem; margin: 0 0 .25rem; }
h2 { font-size: 1.05rem; margin: 2.25rem 0 .6rem; font-weight: 600; }
p.sub { color: #6b6862; margin: 0 0 2rem; }
table { border-collapse: collapse; width: 100%; font-size: 13.5px; }
th, td { text-align: left; padding: .45rem .6rem; border-bottom: 1px solid #e6e3dd;
         vertical-align: top; }
th { font-weight: 600; color: #6b6862; font-size: 12px; text-transform: uppercase;
     letter-spacing: .04em; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
.tag { display: inline-block; padding: .05rem .45rem; border-radius: 3px;
       font-size: 12px; font-weight: 600; }
.ok   { background: #dcefe0; color: #17492a; }
.bad  { background: #f7dcd9; color: #7d2119; }
.warn { background: #f8ecd3; color: #6d4c15; }
.run  { background: #e3e6ef; color: #2c3552; }
.cards { display: flex; flex-wrap: wrap; gap: .75rem; margin-bottom: .5rem; }
.card { border: 1px solid #e6e3dd; border-radius: 6px; padding: .7rem .9rem;
        min-width: 9.5rem; background: #fff; }
.card .label { font-size: 11.5px; text-transform: uppercase; letter-spacing: .04em;
               color: #6b6862; }
.card .value { font-size: 1.35rem; font-variant-numeric: tabular-nums; margin-top: .15rem; }
footer { margin-top: 3rem; color: #8a867e; font-size: 12.5px; }
.wrap { overflow-x: auto; }
@media (prefers-color-scheme: dark) {
  body { background: #16161a; color: #e7e5e0; }
  th, td { border-bottom-color: #2c2c33; }
  th, p.sub, .card .label, footer { color: #9a978f; }
  .card { background: #1d1d22; border-color: #2c2c33; }
  .ok { background: #1c3a27; color: #a9dcb8; }
  .bad { background: #46201c; color: #f0b3aa; }
  .warn { background: #453518; color: #e8c98a; }
  .run { background: #262b3d; color: #b9c2de; }
}
"""


def _fetch(conn):
    data = {}
    with conn.cursor() as cur:
        cur.execute(
            """
            select run_id, job, window_start, window_end, started_at, finished_at,
                   status, error
              from ops.pipeline_runs
             order by run_id desc
             limit 20
            """
        )
        data["runs"] = cur.fetchall()

        cur.execute(
            """
            select run_id, metric, value from ops.run_metrics
             where run_id in (select run_id from ops.pipeline_runs
                              order by run_id desc limit 20)
            """
        )
        metrics = {}
        for run_id, metric, value in cur.fetchall():
            metrics.setdefault(run_id, {})[metric] = value
        data["metrics"] = metrics

        cur.execute(
            """
            select started_at, finished_at, run_id
              from ops.pipeline_runs
             where status = 'succeeded'
             order by run_id desc limit 1
            """
        )
        data["last_success"] = cur.fetchone()

        cur.execute(
            """
            select check_name, severity,
                   count(*) filter (where status = 'pass') as passes,
                   count(*) filter (where status = 'fail') as fails,
                   max(created_at) filter (where status = 'fail') as last_failed
              from ops.test_results
             group by check_name, severity
             order by fails desc, check_name
            """
        )
        data["checks"] = cur.fetchall()

        cur.execute("select count(*) from raw.studies")
        data["raw_rows"] = cur.fetchone()[0]
        cur.execute("select count(*) from raw.studies_history")
        data["history_rows"] = cur.fetchone()[0]
        cur.execute("select count(*) from marts.fct_trials")
        data["mart_rows"] = cur.fetchone()[0]
    return data


def _fmt(value):
    if value is None:
        return "&mdash;"
    if isinstance(value, (dt.datetime,)):
        return value.strftime("%Y-%m-%d %H:%M UTC")
    if isinstance(value, dt.date):
        return value.isoformat()
    return html.escape(str(value))


def _num(value):
    if value is None:
        return "&mdash;"
    return f"{int(value):,}"


def render(conn) -> str:
    d = _fetch(conn)
    generated = dt.datetime.now(dt.timezone.utc)

    last_success = d["last_success"]
    if last_success:
        age_hours = (generated - last_success[1].replace(tzinfo=dt.timezone.utc)).total_seconds() / 3600
        success_str = f"{_fmt(last_success[1])} ({age_hours:.1f}h ago)"
    else:
        success_str = "never"

    rows_html = []
    for run_id, job, w_start, w_end, started, finished, status, error in d["runs"]:
        m = d["metrics"].get(run_id, {})
        klass = {"succeeded": "ok", "failed": "bad", "running": "run"}.get(status, "warn")
        rows_html.append(
            "<tr>"
            f"<td class='num'>{run_id}</td>"
            f"<td>{html.escape(job)}</td>"
            f"<td><span class='tag {klass}'>{status}</span></td>"
            f"<td>{_fmt(w_start)} &rarr; {_fmt(w_end)}</td>"
            f"<td>{_fmt(started)}</td>"
            f"<td class='num'>{_num(m.get('studies_fetched'))}</td>"
            f"<td class='num'>{_num(m.get('studies_new'))}</td>"
            f"<td class='num'>{_num(m.get('studies_changed'))}</td>"
            f"<td class='num'>{_num(m.get('studies_unchanged'))}</td>"
            f"<td class='num'>{_num(m.get('api_requests'))}</td>"
            f"<td class='num'>{_num(m.get('checks_failed_error'))}"
            f" / {_num(m.get('checks_failed_warn'))}</td>"
            f"<td>{html.escape((error or '')[:90])}</td>"
            "</tr>"
        )

    checks_html = []
    for name, severity, passes, fails, last_failed in d["checks"]:
        total = passes + fails
        rate = f"{(passes / total * 100):.0f}%" if total else "&mdash;"
        klass = "ok" if fails == 0 else ("bad" if severity == "error" else "warn")
        checks_html.append(
            "<tr>"
            f"<td>{html.escape(name)}</td>"
            f"<td><span class='tag {klass}'>{severity}</span></td>"
            f"<td class='num'>{passes}</td>"
            f"<td class='num'>{fails}</td>"
            f"<td class='num'>{rate}</td>"
            f"<td>{_fmt(last_failed)}</td>"
            "</tr>"
        )

    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>clinical-trials-pipeline &middot; status</title>
<style>{CSS}</style>
</head><body><main>

<h1>clinical-trials-pipeline</h1>
<p class="sub">Daily incremental load of ClinicalTrials.gov into Postgres.
Page regenerated at the end of every run &mdash; {_fmt(generated)}.</p>

<div class="cards">
  <div class="card"><div class="label">Last success</div><div class="value" style="font-size:1rem">{success_str}</div></div>
  <div class="card"><div class="label">Studies (current)</div><div class="value">{_num(d['raw_rows'])}</div></div>
  <div class="card"><div class="label">Versions kept</div><div class="value">{_num(d['history_rows'])}</div></div>
  <div class="card"><div class="label">Rows in fct_trials</div><div class="value">{_num(d['mart_rows'])}</div></div>
</div>

<h2>Recent runs</h2>
<div class="wrap"><table>
<thead><tr>
<th>Run</th><th>Job</th><th>Status</th><th>Window</th><th>Started</th>
<th>Fetched</th><th>New</th><th>Changed</th><th>Unchanged</th><th>API calls</th>
<th>Fail err/warn</th><th>Error</th>
</tr></thead>
<tbody>{"".join(rows_html) or "<tr><td colspan='12'>No runs yet.</td></tr>"}</tbody>
</table></div>

<h2>Data quality checks, all time</h2>
<div class="wrap"><table>
<thead><tr><th>Check</th><th>Severity</th><th>Passes</th><th>Fails</th><th>Pass rate</th><th>Last failed</th></tr></thead>
<tbody>{"".join(checks_html) or "<tr><td colspan='6'>No checks run yet.</td></tr>"}</tbody>
</table></div>

<footer>
Every failure this pipeline has had is written up in
<a href="https://github.com/Divyadhole/clinical-trials-pipeline/blob/main/INCIDENTS.md">INCIDENTS.md</a>.
</footer>
</main></body></html>
"""


def write(conn, path: pathlib.Path = None) -> pathlib.Path:
    path = path or OUTPUT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(conn), encoding="utf-8")
    return path
