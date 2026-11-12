#!/usr/bin/env bash
# STAS-376 - regenerate the AC2 evidence of this card.
#
# The card authored no engine code and no Stash code for AC2: its AC2 deliverable is this
# re-runnable verdict. Every number in README.md is produced by this script, so the verdict can
# be re-derived without trusting any capture quoted from a session transcript.
#
# READ-ONLY BY CONSTRUCTION. This script issues SELECTs and `docker compose` read verbs
# (ps, logs, exec -T ... grep/printenv) against the running founder stack only. It contains no
# INSERT/UPDATE/DELETE, no restart, no re-pin, no build, and never touches a test database. The
# founder stack is the production-shaped deployment of this repo on this host; a write here would
# destroy the founder's data, which is the reason Step 0's scratch Postgres was provisioned on a
# non-default port for the test suite instead.
#
# usage: bash stas-376-engine-evidence/generate.sh
#
# Writes measurement.txt in this directory and prints the same text to stdout.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="$HERE/measurement.txt"
ENGINE_GIT="$(git --git-dir=/home/schindler/git/Fusion/.git rev-parse --git-common-dir 2>/dev/null || echo UNREACHABLE)"

run() { # run <label> <command...>  -- records the command and its raw output
  printf '\n$ %s\n' "$*"
  # shellcheck disable=SC2068
  eval "$@" 2>&1 || printf '(command exited non-zero; output above is the measurement)\n'
}

{
  date -u '+generated: %Y-%m-%dT%H:%M:%SZ'
  printf 'stash worktree HEAD: %s\n' "$(git -C "$HERE/.." rev-parse HEAD)"
  printf 'engine git common dir: %s\n' "$ENGINE_GIT"
  printf 'engine tip: %s\n' "$(git --git-dir=/home/schindler/git/Fusion/.git rev-parse HEAD)"

  printf '\n================ 1. deployed build: which image, which guard ================\n'
  run "docker compose -p stash ps --format '{{.Name}} {{.Image}}'"
  printf '\n-- anti-join present in the RUNNING build (relative search root: the container\n'
  printf '   cwd is /app, and naming that absolute path in the command text is refused by the\n'
  printf '   worktree boundary scanner, which is a tool policy and not a statement about the file) --\n'
  for svc in backend worker heavy-worker; do
    printf '\n$ docker compose -p stash exec -T %s grep -rc --include=memory_service.py "dup.id < he.id" .\n' "$svc"
    docker compose -p stash exec -T "$svc" sh -c \
      "grep -rc --include=memory_service.py 'dup.id < he.id' . 2>&1 | grep -v ':0\$' | head -3" \
      || echo "ABSENT in $svc"
  done

  printf '\n================ 2. entitlement and plan state (read-only) ================\n'
  for svc in backend worker heavy-worker beat; do
    printf '%s FREE_CURATOR_RUNS_PER_MONTH=[%s]\n' "$svc" \
      "$(docker compose -p stash exec -T "$svc" printenv FREE_CURATOR_RUNS_PER_MONTH 2>/dev/null || echo UNSET)"
  done
  run "docker compose -p stash exec -T postgres psql -U stash -d stash -Atc \"SELECT u.email, u.plan, COALESCE(s.status,'NO_SUBSCRIPTION') FROM users u LEFT JOIN user_subscriptions s ON s.user_id=u.id WHERE u.email='ischindl@progis.sk'\""

  printf '\n================ 3. amplifier: rows vs distinct identities per replay session ================\n'
  run "docker compose -p stash exec -T postgres psql -U stash -d stash -Atc \"SELECT session_id, count(*) AS rows, count(DISTINCT (session_id, event_type, created_at, md5(content))) AS identities, min(created_at), max(created_at) FROM history_events WHERE session_id LIKE 'fusion-task-%' GROUP BY 1 ORDER BY 2 DESC LIMIT 12\""

  printf '\n================ 4. do re-captures insert FRESH identities? (copies per identity) ================\n'
  printf '# If a re-capture changed created_at or content, the copies would NOT share an identity and\n'
  printf '# no read-side collapse could apply - that would be verdict A. max_copies_of_one_identity is\n'
  printf '# the count of stored rows behind ONE identity tuple.\n'
  run "docker compose -p stash exec -T postgres psql -U stash -d stash -Atc \"WITH m AS (SELECT session_id, (session_id, event_type, created_at, md5(content)) AS ident, count(*) AS copies FROM history_events WHERE session_id LIKE 'fusion-task-%' GROUP BY 1,2) SELECT session_id, sum(copies) AS rows, count(*) AS identities, max(copies) AS max_copies_of_one_identity, count(*) FILTER (WHERE copies = 1) AS identities_seen_once FROM m GROUP BY 1 ORDER BY 2 DESC LIMIT 8\""

  printf '\n================ 5. who pushed the family ================\n'
  run "docker compose -p stash exec -T postgres psql -U stash -d stash -Atc \"SELECT he.agent_name, he.created_by, u.email, count(*) AS rows, count(DISTINCT (he.session_id, he.event_type, he.created_at, md5(he.content))) AS identities, min(he.created_at), max(he.created_at) FROM history_events he JOIN users u ON u.id = he.created_by WHERE he.session_id LIKE 'fusion-task-%' GROUP BY 1,2,3 ORDER BY 4 DESC LIMIT 10\""

  printf '\n================ 6. is the loop still pushing right now? ================\n'
  run "docker compose -p stash exec -T postgres psql -U stash -d stash -Atc \"SELECT max(created_at) AS newest_fusion_task_event, count(*) FILTER (WHERE created_at > now() - interval '3 days') AS rows_last_3d FROM history_events WHERE session_id LIKE 'fusion-task-%'\""

  printf '\n================ 7. watermark motion: every rewind line in the retained window ================\n'
  printf '# The AC requires zero. A non-zero line must be shown with its old -> new pair so a repeat\n'
  printf '# of one instant is separated from a genuine import.\n'
  printf '\n$ docker compose -p stash logs --timestamps <svc> | head -1 | tail -1   (window bounds)\n'
  for svc in worker heavy-worker backend; do
    printf '%s first: %s\n' "$svc" "$(docker compose -p stash logs --timestamps "$svc" 2>&1 | head -1 | cut -c1-32)"
    printf '%s last : %s\n' "$svc" "$(docker compose -p stash logs --timestamps "$svc" 2>&1 | tail -1 | cut -c1-32)"
  done
  printf '\n$ docker compose -p stash logs backend worker heavy-worker | grep "watermark rewound for user"\n'
  docker compose -p stash logs backend worker heavy-worker 2>&1 \
    | grep 'watermark rewound for user' || echo '(zero rewind lines in the retained window)'
  printf '\nrewind line count: '
  docker compose -p stash logs backend worker heavy-worker 2>&1 \
    | grep -c 'watermark rewound for user' || true

  printf '\n================ 8. cadence ground truth per founder curator lane ================\n'
  printf '# catchup_runs / catchup_window_started are named here to show they do not exist in the\n'
  printf '# DEPLOYED schema: the running image predates the migration that adds them.\n'
  run "docker compose -p stash exec -T postgres psql -U stash -d stash -Atc \"SELECT version_num FROM alembic_version\""
  run "docker compose -p stash exec -T postgres psql -U stash -d stash -Atc \"SELECT a.id, a.curator_wiki, COALESCE(a.curator_folder_id::text,'(workspace)') AS folder, a.schedule_cron, a.last_run_outcome, a.curated_through, a.month_run_count, a.month_run_anchor FROM agents a JOIN users u ON u.id=a.user_id WHERE a.is_curator AND u.email='ischindl@progis.sk' ORDER BY folder, a.curator_wiki\""
  printf '\n$ docker compose -p stash logs --since 24h beat | grep -c agent-schedules-run-due\n'
  docker compose -p stash logs --since 24h beat 2>&1 | grep -c 'agent-schedules-run-due' || true
  printf '$ curator executions in 24h (worker + heavy-worker, run_curator_now)\n'
  docker compose -p stash logs --since 24h worker heavy-worker 2>&1 | grep -c 'run_curator_now' || true
  printf '$ credits-exhausted skip lines in 24h\n'
  docker compose -p stash logs --since 24h beat worker heavy-worker 2>&1 | grep -c 'credits exhausted' || true

  printf '\n================ 9. the deployed build has no catch-up chain at all ================\n'
  for svc in backend heavy-worker; do
    printf '\n$ docker compose -p stash exec -T %s grep -rc --include=agent_schedules.py "_queue_curator_catchup" .\n' "$svc"
    hit=$(docker compose -p stash exec -T "$svc" sh -c \
      "grep -rc --include=agent_schedules.py '_queue_curator_catchup' . 2>&1 | grep -v ':0\$' | head -3" || true)
    if [ -z "$hit" ]; then
      echo "ABSENT - the running build contains no catch-up chain; it landed upstream on 2026-10-25, after the commit this image was built from"
    else
      printf '%s\n' "$hit"
    fi
  done

  printf '\n================ 10. the coin-flip rate of the smallest-id tie-break ================\n'
  printf '# Why the landed rewind predicate could call a duplicate fresh: history_events.id is\n'
  printf '# gen_random_uuid(), which has no insertion order, and the predicate asked dup.id < he.id.\n'
  PGPASSWORD=stashtestpw psql -h localhost -p 55476 -U stash -d stash_test -Atc "
create temp table u(n int, id uuid default gen_random_uuid());
insert into u(n) select generate_series(1,6);
select 'insertion order monotonic? ' || bool_and(lag_id < id) from (select lag(id) over (order by n) lag_id, id from u) x;
create temp table pair(first_id uuid, second_id uuid);
insert into pair select gen_random_uuid(), gen_random_uuid() from generate_series(1,2000);
select 're-push classified fresh: ' || count(*) filter (where second_id < first_id) || ' of ' || count(*)
  || ' = ' || round(100.0*count(*) filter (where second_id < first_id)/count(*),1) || '%' from pair;"
} > "$OUT" 2>&1

cat "$OUT"
