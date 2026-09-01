-- Marts: the layer something else would actually query. Deliberately small.
-- Fifty thousand rows does not need a warehouse; it needs a clean grain and
-- keys that hold.

create schema if not exists marts;

create table if not exists marts.dim_sponsor (
    sponsor_key   bigserial primary key,
    lead_sponsor  text not null unique,
    sponsor_class text
);

create table if not exists marts.fct_trials (
    nct_id                text primary key,
    sponsor_key           bigint references marts.dim_sponsor (sponsor_key),
    overall_status        text,
    study_type            text,
    phase                 text,
    enrollment            integer,
    start_date            date,
    completion_date       date,
    last_update_post_date date not null,
    has_results           boolean,
    condition_count       integer not null default 0,
    intervention_count    integer not null default 0,
    eligibility_char_len  integer,
    built_at              timestamptz not null default now()
);

create index if not exists ix_fct_trials_status on marts.fct_trials (overall_status);
create index if not exists ix_fct_trials_sponsor on marts.fct_trials (sponsor_key);

create table if not exists marts.bridge_trial_condition (
    nct_id    text not null references marts.fct_trials (nct_id) on delete cascade,
    condition text not null,
    primary key (nct_id, condition)
);

create or replace view marts.v_trials_by_status_month as
select
    date_trunc('month', last_update_post_date)::date as update_month,
    overall_status,
    count(*)                                          as trial_count,
    sum(coalesce(enrollment, 0))                      as total_enrollment
from marts.fct_trials
group by 1, 2;
