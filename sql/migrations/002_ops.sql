-- Observability layer. Every run, every metric, every data quality result.
-- The status page is a read of these four tables and nothing else.

create schema if not exists ops;

create table if not exists ops.pipeline_runs (
    run_id       bigserial primary key,
    job          text        not null,
    window_start date,
    window_end   date,
    started_at   timestamptz not null default now(),
    finished_at  timestamptz,
    status       text        not null default 'running'
                 check (status in ('running', 'succeeded', 'failed')),
    error        text
);

create index if not exists ix_runs_started on ops.pipeline_runs (started_at desc);

create table if not exists ops.run_metrics (
    run_id bigint  not null references ops.pipeline_runs (run_id) on delete cascade,
    metric text    not null,
    value  numeric not null,
    primary key (run_id, metric)
);

create table if not exists ops.test_results (
    id         bigserial primary key,
    run_id     bigint references ops.pipeline_runs (run_id) on delete cascade,
    check_name text not null,
    target     text not null,
    severity   text not null check (severity in ('error', 'warn')),
    status     text not null check (status in ('pass', 'fail')),
    observed   numeric,
    threshold  numeric,
    detail     text,
    created_at timestamptz not null default now()
);

create index if not exists ix_test_results_run on ops.test_results (run_id);

-- One row per stream. The daily job reads this to decide where to start and
-- only advances it after the load and the quality gate both pass.
create table if not exists ops.watermarks (
    stream     text primary key,
    last_value date        not null,
    updated_at timestamptz not null default now()
);

-- Schema drift detection. We store the sorted set of JSON paths seen in a
-- sample of each run's payloads. A new or vanished path is a signal that the
-- upstream contract moved, which is the failure mode that silently produces
-- nulls for weeks if nobody is watching.
create table if not exists ops.schema_fingerprints (
    id          bigserial primary key,
    observed_at timestamptz not null default now(),
    run_id      bigint references ops.pipeline_runs (run_id) on delete set null,
    fingerprint text  not null,
    field_paths jsonb not null
);
