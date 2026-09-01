-- Staging: typed, flattened, one row per study. Still one-to-one with raw.
-- All interpretation of the API's nested modules happens on the way in here.

create schema if not exists staging;

create table if not exists staging.studies (
    nct_id                text primary key,
    brief_title           text,
    official_title        text,
    overall_status        text,
    study_type            text,
    phase                 text,
    enrollment            integer,
    enrollment_type       text,
    lead_sponsor          text,
    sponsor_class         text,
    start_date            date,
    completion_date       date,
    last_update_post_date date not null,
    eligibility_criteria  text,
    healthy_volunteers    boolean,
    sex                   text,
    minimum_age_text      text,
    maximum_age_text      text,
    has_results           boolean,
    loaded_at             timestamptz not null default now()
);

create index if not exists ix_staging_status on staging.studies (overall_status);

create table if not exists staging.study_conditions (
    nct_id    text not null references staging.studies (nct_id) on delete cascade,
    condition text not null,
    primary key (nct_id, condition)
);

create table if not exists staging.study_interventions (
    nct_id            text not null references staging.studies (nct_id) on delete cascade,
    intervention_type text not null,
    intervention_name text not null,
    primary key (nct_id, intervention_type, intervention_name)
);
