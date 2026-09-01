-- Raw layer: the API response, stored as received, one current row per study.
-- Nothing here is interpreted. If a downstream assumption turns out wrong,
-- this table is what lets us rebuild without re-crawling the API.

create schema if not exists raw;

create table if not exists raw.studies (
    nct_id                text primary key,
    last_update_post_date date        not null,
    content_hash          text        not null,
    payload               jsonb       not null,
    first_seen_at         timestamptz not null default now(),
    last_seen_at          timestamptz not null default now(),
    last_changed_at       timestamptz not null default now(),
    first_seen_run_id     bigint,
    last_seen_run_id      bigint
);

create index if not exists ix_raw_studies_last_update
    on raw.studies (last_update_post_date);

-- Append-only record of every distinct version of a study we have seen.
-- Studies on ClinicalTrials.gov are revised after registration, so a record
-- loaded in week 1 can reappear in week 6 with different content. A row lands
-- here only when the content hash is new, which is what makes re-running a
-- day a no-op instead of a duplicate.
create table if not exists raw.studies_history (
    nct_id                text        not null,
    content_hash          text        not null,
    last_update_post_date date        not null,
    payload               jsonb       not null,
    captured_at           timestamptz not null default now(),
    run_id                bigint,
    primary key (nct_id, content_hash)
);

create index if not exists ix_raw_history_captured
    on raw.studies_history (captured_at);
