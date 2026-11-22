-- User-owned platform records. Requires the evidence migration first.
create schema if not exists investiq_app;
revoke all on schema investiq_app from public, anon, authenticated;
grant usage on schema investiq_app to authenticated;

create table investiq_app.consent_events (
 id uuid primary key default gen_random_uuid(), owner_id uuid not null references auth.users(id),
 granted boolean not null, policy_version text not null, created_at timestamptz not null default clock_timestamp()
);
create table investiq_app.financial_snapshots (
 id uuid primary key default gen_random_uuid(), owner_id uuid not null references auth.users(id),
 income numeric not null check(income>=0), expenses numeric not null check(expenses>=0),
 assets numeric not null check(assets>=0), liabilities numeric not null check(liabilities>=0),
 goal numeric check(goal>=0), uncertainty_pct numeric check(uncertainty_pct > 0 and uncertainty_pct <= 100), currency text not null check(currency='INR'),
 created_at timestamptz not null default clock_timestamp(), unique(owner_id,id)
);
create table investiq_app.audit_events (
 id uuid primary key default gen_random_uuid(), owner_id uuid not null references auth.users(id),
 action text not null, subject_id uuid, details jsonb not null default '{}'::jsonb,
 created_at timestamptz not null default clock_timestamp()
);
create table investiq_app.forecasts (
 id uuid primary key default gen_random_uuid(), owner_id uuid not null references auth.users(id),
 event text not null check(length(trim(event)) between 1 and 300),
 probability numeric not null check(probability>=0 and probability<=1), target_at timestamptz not null,
 model_version text not null check(length(trim(model_version)) between 1 and 100),
 evidence_claim_id uuid not null, issued_at timestamptz not null default clock_timestamp(),
 unique(owner_id,id), foreign key(owner_id,evidence_claim_id) references investiq_evidence.claims(owner_id,id),
 check(target_at > issued_at)
);
create table investiq_app.forecast_resolutions (
 id uuid primary key default gen_random_uuid(), owner_id uuid not null references auth.users(id),
 forecast_id uuid not null, evidence_claim_id uuid not null, outcome boolean not null,
 resolved_at timestamptz not null default clock_timestamp(), unique(owner_id,forecast_id),
 foreign key(owner_id,forecast_id) references investiq_app.forecasts(owner_id,id),
 foreign key(owner_id,evidence_claim_id) references investiq_evidence.claims(owner_id,id)
);

create function investiq_app.check_forecast_evidence() returns trigger language plpgsql
set search_path=pg_catalog,investiq_app,investiq_evidence as $$
declare c investiq_evidence.claims%rowtype; d investiq_evidence.documents%rowtype; f investiq_app.forecasts%rowtype;
begin
 select * into c from investiq_evidence.claims where owner_id=new.owner_id and id=new.evidence_claim_id;
 select * into d from investiq_evidence.documents where owner_id=new.owner_id and id=c.document_id;
 if c.verification_status <> 'human_verified' or d.available_at > clock_timestamp() or d.retrieved_at > clock_timestamp() then
   raise exception 'verified available evidence required';
 end if;
 if tg_table_name = 'forecast_resolutions' then
   select * into f from investiq_app.forecasts where owner_id=new.owner_id and id=new.forecast_id;
   if clock_timestamp() < f.target_at or d.available_at <= f.issued_at then raise exception 'outcome not eligible for resolution'; end if;
 end if;
 return new;
end $$;
create trigger forecast_evidence before insert on investiq_app.forecasts for each row execute function investiq_app.check_forecast_evidence();
create trigger resolution_evidence before insert on investiq_app.forecast_resolutions for each row execute function investiq_app.check_forecast_evidence();

create function investiq_app.set_server_time() returns trigger language plpgsql set search_path=pg_catalog as $$
begin
 if tg_table_name='forecasts' then new.issued_at=clock_timestamp();
 elsif tg_table_name='forecast_resolutions' then new.resolved_at=clock_timestamp();
 else new.created_at=clock_timestamp(); end if;
 return new;
end $$;
create function investiq_app.reject_mutation() returns trigger language plpgsql set search_path=pg_catalog as $$
begin raise exception 'append-only records'; end $$;

do $$ declare t text; begin
 foreach t in array array['consent_events','financial_snapshots','audit_events','forecasts','forecast_resolutions'] loop
  execute format('create trigger server_time before insert on investiq_app.%I for each row execute function investiq_app.set_server_time()',t);
  execute format('create trigger immutable_rows before update or delete on investiq_app.%I for each row execute function investiq_app.reject_mutation()',t);
  execute format('alter table investiq_app.%I enable row level security',t);
  execute format('alter table investiq_app.%I force row level security',t);
  execute format('revoke all on investiq_app.%I from public,anon,authenticated',t);
  execute format('grant select,insert on investiq_app.%I to authenticated',t);
  if t='financial_snapshots' then
   execute format('create policy owner_read on investiq_app.%I for select to authenticated using(owner_id=(select auth.uid()) and coalesce((select granted from investiq_app.consent_events where owner_id=(select auth.uid()) order by created_at desc,id desc limit 1),false))',t);
  else
   execute format('create policy owner_read on investiq_app.%I for select to authenticated using(owner_id=(select auth.uid()))',t);
  end if;
  if t='financial_snapshots' then
   execute format('create policy owner_insert on investiq_app.%I for insert to authenticated with check(owner_id=(select auth.uid()) and coalesce((select granted from investiq_app.consent_events where owner_id=(select auth.uid()) order by created_at desc,id desc limit 1),false))',t);
  else
   execute format('create policy owner_insert on investiq_app.%I for insert to authenticated with check(owner_id=(select auth.uid()))',t);
  end if;
 end loop;
end $$;
