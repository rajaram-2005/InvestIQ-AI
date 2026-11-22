-- Incremental, user-owned planning records; apply after both earlier migrations.
create table investiq_app.savings_missions (
 id uuid primary key default gen_random_uuid(), owner_id uuid not null references auth.users(id),
 title text not null check (length(trim(title)) between 1 and 100),
 start_date date not null, months integer not null check (months between 1 and 600),
 target numeric(16,2) not null check(target > 0),
 monthly_contribution numeric(16,2) not null check(monthly_contribution >= 0),
 monthly_income numeric(16,2) not null check(monthly_income >= 0),
 personal_expenses numeric(16,2) not null check(personal_expenses >= 0),
 learning_expenses numeric(16,2) not null check(learning_expenses >= 0),
 emergency_expenses numeric(16,2) not null check(emergency_expenses >= 0),
 benefits_confirmed boolean not null default false,
 created_at timestamptz not null default clock_timestamp(), unique(owner_id,id)
);
create table investiq_app.savings_contributions (
 id uuid primary key default gen_random_uuid(), owner_id uuid not null references auth.users(id),
 mission_id uuid not null, amount numeric(16,2) not null check(amount > 0), contribution_date date not null,
 created_at timestamptz not null default clock_timestamp(),
 foreign key(owner_id,mission_id) references investiq_app.savings_missions(owner_id,id)
);
-- The latest mission is the active plan. New plans preserve prior plan/history; no silent edits.
do $$ declare t text; begin
 foreach t in array array['savings_missions','savings_contributions'] loop
  execute format('create trigger server_time before insert on investiq_app.%I for each row execute function investiq_app.set_server_time()',t);
  execute format('create trigger immutable_rows before update or delete on investiq_app.%I for each row execute function investiq_app.reject_mutation()',t);
  execute format('alter table investiq_app.%I enable row level security',t);
  execute format('alter table investiq_app.%I force row level security',t);
  execute format('revoke all on investiq_app.%I from public,anon,authenticated',t);
  execute format('grant select,insert on investiq_app.%I to authenticated',t);
  execute format('create policy owner_read on investiq_app.%I for select to authenticated using(owner_id=(select auth.uid()) and coalesce((select granted from investiq_app.consent_events where owner_id=(select auth.uid()) order by created_at desc,id desc limit 1),false))',t);
  execute format('create policy owner_insert on investiq_app.%I for insert to authenticated with check(owner_id=(select auth.uid()) and coalesce((select granted from investiq_app.consent_events where owner_id=(select auth.uid()) order by created_at desc,id desc limit 1),false))',t);
 end loop;
end $$;
create index savings_missions_owner_created_idx on investiq_app.savings_missions(owner_id,created_at desc,id desc);
create index savings_contributions_owner_mission_date_idx on investiq_app.savings_contributions(owner_id,mission_id,contribution_date);
