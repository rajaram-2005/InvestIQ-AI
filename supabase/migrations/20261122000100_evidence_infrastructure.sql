-- Apply to a Supabase PostgreSQL project with auth.uid(), authenticated,
-- anon, service_role and pgvector available. All data is tenant-private.
create schema if not exists extensions;
create extension if not exists vector with schema extensions;
do $$ begin
  if not exists (select 1 from pg_extension e join pg_namespace n on n.oid=e.extnamespace
                 where e.extname='vector' and n.nspname='extensions') then
    raise exception 'pgvector must be installed in the extensions schema; do not relocate a shared extension automatically';
  end if;
end $$;
create schema if not exists investiq_evidence;
revoke all on schema investiq_evidence from public, anon, authenticated;
grant usage on schema investiq_evidence to authenticated, service_role;

create table investiq_evidence.sources (
    id uuid primary key,
    owner_id uuid not null references auth.users(id),
    provider text not null check (length(trim(provider)) > 0),
    source_uri text not null check (length(trim(source_uri)) > 0),
    license text not null check (length(trim(license)) > 0),
    license_reference text not null check (length(trim(license_reference)) > 0),
    allow_raw_storage boolean not null,
    allow_derived_storage boolean not null check (allow_derived_storage),
    allow_redistribution boolean not null default false,
    version_identifier text not null check (length(trim(version_identifier)) > 0),
    created_at timestamptz not null default clock_timestamp(),
    unique(owner_id, id)
);

create table investiq_evidence.documents (
    id uuid primary key,
    owner_id uuid not null,
    source_id uuid not null,
    original_source_uri text not null check (length(trim(original_source_uri)) > 0),
    document_type text not null check (document_type in ('financial_document','market_prices','fund_nav','financial_statement','corporate_announcement')),
    media_type text not null check (media_type in ('text/plain','application/pdf','text/csv','application/json')),
    content_sha256 text not null check (content_sha256 ~ '^[0-9a-f]{64}$'),
    raw_content bytea,
    extracted_text text,
    extractor_version text,
    published_at timestamptz not null,
    available_at timestamptz not null check (available_at >= published_at),
    retrieved_at timestamptz not null default clock_timestamp(),
    version_identifier text not null check (length(trim(version_identifier)) > 0),
    unique(owner_id, id), unique(owner_id, id, source_id),
    foreign key(owner_id, source_id) references investiq_evidence.sources(owner_id, id)
);

create function investiq_evidence.set_retrieval_clock() returns trigger
language plpgsql set search_path = pg_catalog as $$
begin new.retrieved_at := clock_timestamp(); return new; end $$;

create trigger document_retrieval_clock before insert on investiq_evidence.documents
for each row execute function investiq_evidence.set_retrieval_clock();

create function investiq_evidence.check_document_rights() returns trigger
language plpgsql set search_path = pg_catalog, investiq_evidence as $$
begin
    if (new.raw_content is not null or new.extracted_text is not null) and not exists (
        select 1 from investiq_evidence.sources s
        where s.owner_id = new.owner_id and s.id = new.source_id and s.allow_raw_storage
    ) then raise exception 'raw storage not permitted'; end if;
    if new.raw_content is not null and encode(pg_catalog.sha256(new.raw_content), 'hex') <> new.content_sha256 then
        raise exception 'raw content hash mismatch';
    end if;
    return new;
end $$;
create trigger document_rights before insert on investiq_evidence.documents
for each row execute function investiq_evidence.check_document_rights();

create table investiq_evidence.observations (
    id uuid primary key,
    owner_id uuid not null,
    source_id uuid not null,
    document_id uuid not null,
    logical_key text not null check (length(trim(logical_key)) > 0),
    entity_id text not null check (length(trim(entity_id)) > 0),
    metric text not null check (length(trim(metric)) > 0),
    original_value text not null check (length(original_value) > 0),
    numeric_value numeric not null check (numeric_value::text not in ('NaN','Infinity','-Infinity')),
    currency text not null check (currency ~ '^[A-Z]{3}$'),
    unit text not null check (length(trim(unit)) > 0),
    period_start date not null,
    period_end date not null check (period_end >= period_start),
    published_at timestamptz not null,
    available_at timestamptz not null check (available_at >= published_at),
    retrieved_at timestamptz not null default clock_timestamp(),
    validation_status text not null check (validation_status in ('valid','quarantined')),
    validation_errors jsonb not null check (jsonb_typeof(validation_errors) = 'array'),
    version_identifier text not null check (length(trim(version_identifier)) > 0),
    supersedes_id uuid,
    unique(owner_id, id), unique(owner_id, logical_key, version_identifier),
    foreign key(owner_id, source_id) references investiq_evidence.sources(owner_id, id),
    foreign key(owner_id, document_id, source_id) references investiq_evidence.documents(owner_id, id, source_id),
    foreign key(owner_id, supersedes_id) references investiq_evidence.observations(owner_id, id),
    check ((validation_status = 'valid' and validation_errors = '[]'::jsonb) or
           (validation_status = 'quarantined' and jsonb_array_length(validation_errors) > 0))
);
create trigger observation_retrieval_clock before insert on investiq_evidence.observations
for each row execute function investiq_evidence.set_retrieval_clock();
create index observations_asof_idx on investiq_evidence.observations(owner_id, logical_key, available_at desc, retrieved_at desc);

create function investiq_evidence.check_observation_provenance() returns trigger
language plpgsql set search_path = pg_catalog, investiq_evidence as $$
declare prior investiq_evidence.observations%rowtype;
declare doc investiq_evidence.documents%rowtype;
begin
    select * into doc from investiq_evidence.documents where owner_id = new.owner_id and id = new.document_id;
    if new.published_at < doc.published_at or new.available_at < doc.available_at then
        raise exception 'observation predates source document';
    end if;
    if new.supersedes_id is not null then
        select * into prior from investiq_evidence.observations where owner_id = new.owner_id and id = new.supersedes_id;
        if not found or prior.logical_key <> new.logical_key or prior.source_id <> new.source_id or
           prior.entity_id <> new.entity_id or prior.metric <> new.metric or
           prior.period_start <> new.period_start or prior.period_end <> new.period_end or
           prior.currency <> new.currency or prior.unit <> new.unit or new.available_at < prior.available_at then
            raise exception 'invalid supersession identity or time';
        end if;
    end if;
    return new;
end $$;
create trigger observation_provenance before insert on investiq_evidence.observations
for each row execute function investiq_evidence.check_observation_provenance();

create table investiq_evidence.claims (
    id uuid primary key,
    owner_id uuid not null,
    document_id uuid not null,
    quote text not null check (length(quote) > 0),
    char_start integer not null check (char_start >= 0),
    char_end integer not null check (char_end > char_start),
    extractor_version text not null,
    verification_status text not null check (verification_status in ('unverified','human_verified','rejected')),
    created_at timestamptz not null default clock_timestamp(),
    unique(owner_id, id),
    foreign key(owner_id, document_id) references investiq_evidence.documents(owner_id, id)
);
create function investiq_evidence.check_claim_quote() returns trigger
language plpgsql set search_path = pg_catalog, investiq_evidence as $$
declare text_body text;
begin
    select extracted_text into text_body from investiq_evidence.documents
        where owner_id = new.owner_id and id = new.document_id;
    if text_body is null or substring(text_body from new.char_start + 1 for new.char_end - new.char_start) <> new.quote then
        raise exception 'claim quote does not match extracted text';
    end if;
    return new;
end $$;
create trigger claim_quote before insert on investiq_evidence.claims
for each row execute function investiq_evidence.check_claim_quote();

create table investiq_evidence.announcements (
    id uuid primary key,
    owner_id uuid not null,
    document_id uuid not null,
    event_type text not null check (event_type in ('dividend','results','merger','buyback','guidance')),
    quote text not null check (length(quote) > 0),
    char_start integer not null check (char_start >= 0),
    char_end integer not null check (char_end > char_start),
    extractor_version text not null,
    verification_status text not null check (verification_status in ('unverified','human_verified','rejected')),
    created_at timestamptz not null default clock_timestamp(),
    unique(owner_id, id),
    foreign key(owner_id, document_id) references investiq_evidence.documents(owner_id, id)
);
create trigger announcement_quote before insert on investiq_evidence.announcements
for each row execute function investiq_evidence.check_claim_quote();

create table investiq_evidence.claim_relations (
    id uuid primary key,
    owner_id uuid not null,
    from_claim_id uuid not null,
    to_claim_id uuid not null,
    relation text not null check (relation in ('supports','contradicts','references')),
    rationale text not null,
    created_at timestamptz not null default clock_timestamp(),
    unique(owner_id, id),
    foreign key(owner_id, from_claim_id) references investiq_evidence.claims(owner_id, id),
    foreign key(owner_id, to_claim_id) references investiq_evidence.claims(owner_id, id),
    check (from_claim_id <> to_claim_id)
);
create table investiq_evidence.claim_embeddings (
    id uuid primary key,
    owner_id uuid not null,
    claim_id uuid not null,
    model_identifier text not null check (length(trim(model_identifier)) > 0),
    embedding extensions.vector(384) not null,
    created_at timestamptz not null default clock_timestamp(),
    unique(owner_id, id), unique(owner_id, claim_id, model_identifier),
    foreign key(owner_id, claim_id) references investiq_evidence.claims(owner_id, id)
);

-- Each manifest is generated in one repeatable-read transaction and stored in
-- one immutable row. No later membership inserts are possible.
create table investiq_evidence.snapshots (
    id uuid primary key,
    owner_id uuid not null references auth.users(id),
    as_of timestamptz not null,
    created_at timestamptz not null default clock_timestamp(),
    manifest_version text not null,
    manifest jsonb not null,
    manifest_sha256 text not null check (manifest_sha256 ~ '^[0-9a-f]{64}$'),
    unique(owner_id, id)
);

create function investiq_evidence.set_creation_clock() returns trigger
language plpgsql set search_path = pg_catalog as $$
begin new.created_at := clock_timestamp(); return new; end $$;

create function investiq_evidence.reject_mutation() returns trigger
language plpgsql set search_path = pg_catalog as $$
begin raise exception 'append-only evidence: updates and deletes prohibited'; end $$;

do $$ declare t text; begin
    foreach t in array array['sources','documents','observations','claims','announcements','claim_relations','claim_embeddings','snapshots'] loop
        if t not in ('documents','observations') then
            execute format('create trigger creation_clock before insert on investiq_evidence.%I for each row execute function investiq_evidence.set_creation_clock()', t);
        end if;
        execute format('create trigger immutable_rows before update or delete on investiq_evidence.%I for each row execute function investiq_evidence.reject_mutation()', t);
        execute format('alter table investiq_evidence.%I enable row level security', t);
        execute format('alter table investiq_evidence.%I force row level security', t);
        execute format('create policy owner_read on investiq_evidence.%I for select to authenticated using (owner_id = (select auth.uid()))', t);
        execute format('revoke all on investiq_evidence.%I from public, anon, authenticated', t);
        execute format('grant select on investiq_evidence.%I to authenticated', t);
        execute format('grant select, insert on investiq_evidence.%I to service_role', t);
    end loop;
end $$;
-- The service_role bypasses RLS. It must never be supplied to a client.
-- It is trusted infrastructure, and must authorize owner_id before every call.
