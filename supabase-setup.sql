-- Supabase setup for the Investor Tool portfolio.
-- Each row belongs to exactly one authenticated user; RLS is the enforcement boundary.

create table if not exists public.portfolio_positions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  ticker text not null check (char_length(ticker) between 1 and 20),
  shares numeric(24, 8) not null check (shares > 0),
  purchase_price numeric(24, 8) not null check (purchase_price > 0),
  created_at timestamptz not null default now()
);

create index if not exists portfolio_positions_user_id_idx
  on public.portfolio_positions (user_id);

alter table public.portfolio_positions enable row level security;

-- Remove/recreate policies so this setup is safe to run again.
drop policy if exists "Users can read their own portfolio positions" on public.portfolio_positions;
drop policy if exists "Users can add their own portfolio positions" on public.portfolio_positions;
drop policy if exists "Users can update their own portfolio positions" on public.portfolio_positions;
drop policy if exists "Users can delete their own portfolio positions" on public.portfolio_positions;

create policy "Users can read their own portfolio positions"
  on public.portfolio_positions for select to authenticated
  using (auth.uid() = user_id);

create policy "Users can add their own portfolio positions"
  on public.portfolio_positions for insert to authenticated
  with check (auth.uid() = user_id);

create policy "Users can update their own portfolio positions"
  on public.portfolio_positions for update to authenticated
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

create policy "Users can delete their own portfolio positions"
  on public.portfolio_positions for delete to authenticated
  using (auth.uid() = user_id);

grant select, insert, update, delete on public.portfolio_positions to authenticated;
revoke all on public.portfolio_positions from anon;

-- Allow a signed-in user to permanently delete only their own account.
-- Deleting auth.users cascades to portfolio_positions via user_id.
create or replace function public.delete_my_account()
returns void
language sql
security definer
set search_path = ''
as $function$
  delete from auth.users where id = (select auth.uid());
$function$;

revoke all on function public.delete_my_account() from public;
revoke all on function public.delete_my_account() from anon;
grant execute on function public.delete_my_account() to authenticated;
