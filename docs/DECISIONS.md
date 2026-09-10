# Design decisions, assumptions, and deviations

This records the judgement calls made while building the ROE against the Kiro
specification, and where the implementation intentionally differs from a
literal reading of it.

---

## 1. Solver: two-phase CP-SAT rather than one monolithic model

**Design says:** OR-Tools CP-SAT, minimise distance (primary) then time
(secondary), hard capacity and time-window constraints, soft priority ordering,
110-second solver limit.

**Built:** CP-SAT throughout, decomposed into assignment then per-vehicle
sequencing.

A single CP-SAT model over 500 orders × 50 vehicles does not reliably find good
solutions inside 120 seconds — the arc-variable count is prohibitive. The
cluster-first / route-second decomposition is a standard CVRPTW approach and
keeps *both* phases in CP-SAT as the design specifies:

- Phase 1 enforces the hard capacity constraints, so Properties 12 and 13 hold
  by construction.
- Phase 2 solves each vehicle's TSP-with-time-windows **exactly**, which is
  where nearly all the distance is decided, so the distance objective is met
  where it matters most.

Measured: 500 orders × 50 vehicles in 18.5 s, all orders assigned, zero
capacity or window violations.

## 2. Priority ordering is a hard constraint first, soft only on infeasibility

**Design says:** a soft constraint with a large penalty weight.

**Built:** the sequencing model is solved with priority-before-standard as a
*hard* constraint. Only if that model is infeasible is it re-solved with the
penalised soft version, and the route is flagged `priority_relaxed`.

Property 15 says a standard stop may precede a priority stop "unless a capacity
or time-window constraint requires it". A pure soft constraint cannot
distinguish "the solver traded it away" from "it was impossible"; the
hard-then-soft sequence makes the escape clause *provable* — an inversion can
only appear on a route where the hard model had no solution. `priority_relaxed`
is surfaced in the route detail panel so the dispatcher sees why.

## 3. Co-located orders are grouped before solving, not after

The Stop schema allows one stop to serve several orders at the same location.
Merging them *after* sequencing produced a real bug: two orders at one address
with different windows could be sequenced apart, then merged into a stop whose
intersected window the scheduled ETA no longer satisfied — breaking Property 14.

Orders are now grouped into `DeliveryPoint`s up front, and only when their time
windows actually intersect. The solver then satisfies the merged stop's
combined weight, volume, service time, and intersected window directly.

## 4. Late stops are never persisted — the order is unassigned instead

Requirement 5.4 says every stop's ETA must fall inside its window; Property 14
allows the alternative of an Impossible Order Alert. When no feasible sequence
exists for a cluster, the hardest order is dropped and the cluster re-solved.
Dropped orders keep `status = unassigned` and raise an Impossible Order Alert
with a reason the dispatcher can act on, rather than appearing as a stop that
is already known to be late.

## 5. GeoPoint is the PostgreSQL composite type from the design

`CREATE TYPE geopoint AS (latitude DECIMAL(10,7), longitude DECIMAL(11,7))` is
created by migration `0001` and mapped through a custom SQLAlchemy
`UserDefinedType`. asyncpg round-trips composites natively, so the schema
matches the design exactly rather than being flattened into two columns.

## 6. RBAC reads the role from the database on every request

**Design says:** short-lived JWTs so a role change propagates within 60 seconds
via the next token refresh.

**Built:** the JWT is validated for signature, expiry, issuer, and audience,
then the user record is loaded and **the database role is authoritative**. A
role change therefore takes effect on the user's very next request, comfortably
inside Requirement 17.6's 60 seconds, and a deactivated account is locked out
immediately rather than at token expiry. Short-lived tokens (15 min) and
refresh tokens (24 h) are still configured.

## 7. Alert types extended with `export_failure`

The data dictionary lists three alert types; the design's Alert Routing table
also lists Export Failure (critical) and Connectivity Warning (warning).

- `export_failure` was **added** to the `alerts` type check — it references a
  route, which fits the existing `entity_type` enum, and Requirement 13.4
  requires surfacing it.
- **Connectivity warnings are not alert rows.** They have no entity to
  reference, and Requirements 1.5/2.6 describe a warning that *persists until
  connectivity is restored* — which is state, not an event. They live in an
  `integration_status` table, are pushed over WebSocket, and are readable at
  `GET /api/v1/system/connectivity`. The banner clears itself on recovery.

## 8. Additional tables beyond the design SQL

| Table | Why |
|---|---|
| `error_log` | The design's audit section requires writing audit-write failures to a separate error log, outside the failing transaction. |
| `export_jobs` | Requirement 13.5 needs a manual re-trigger to *cancel an in-progress retry sequence*, which requires the sequence to be addressable state. |
| `integration_status` | Persistent connectivity state (see above). |
| `travel_time_samples` | The design's Redis-cached historical averages, kept in PostgreSQL so the fallback survives a cache flush; Redis remains the hot path. |

Columns added to design tables: `orders.service_duration_min` and
`geocode_confidence`; `vehicles.driver_name`; `routes.data_quality_warning`,
`data_quality_message`, `needs_reoptimisation`, `priority_relaxed`,
`travel_times_updated_at`, `completed_at`, `driver_id`; `stops.departure`,
`distance_from_previous_km`, `travel_time_from_previous_min`,
`has_priority_order`; `users.password_hash`, `full_name`, `active`;
`alerts.context`. Each backs a stated requirement — for example
`routes.priority_relaxed` backs Property 15's escape clause, and
`alerts.context` carries the entity ids the notification panel displays.

## 9. NUL bytes are stripped at the persistence boundary

PostgreSQL cannot store U+0000 in `text` or `jsonb`. A NUL arriving in an
address or an integration payload would fail the audit INSERT — and by
Requirement 16.5 that rolls back the very change being recorded, so one bad
byte could block data entry entirely. A `SafeText` SQLAlchemy type strips the
character on the way in, and the API schemas strip it at the boundary. Found by
a Hypothesis property test.

## 10. Historical travel times are learned from routes, not from the solver matrix

The Mapping Service fallback (Requirement 7.4) averages previously observed
legs. The obvious place to record them — every leg of the matrix the solver
asked for — is the wrong one: a fleet-wide matrix over 50 depots and 500
delivery points has 302,500 legs, almost none of which are ever driven.
Recording them one row at a time pushed a 500 x 50 run past its 120-second
ceiling.

Samples are now taken from the legs of the routes that actually come out of the
solve (~10 per route), written as a single multi-row upsert with a hard cap.
That is both far cheaper and a better estimator: the averages reflect roads the
fleet drives.

## 11. Operating hours are interpreted in UTC

`operating_hours_start/end` are wall-clock `TIME` values with no zone in the
schema. They are combined with the planning date in UTC. A single-region
deployment sets the container timezone; a multi-region fleet would need a
per-depot timezone column, which the specification does not define.

## 12. The planning day

The specification does not define how a planning period is selected. The
planning day is taken from the earliest time window among the orders being
optimised, falling back to today. Vehicle shifts are then anchored to that day.

## 13. Optimisation runs synchronously within the request

`POST /api/v1/optimise` runs the solve and returns the completed run, with
progress pushed over WebSocket at ≤3-second intervals throughout. The
120-second ceiling is enforced with `asyncio.wait_for`, and the solver itself
runs in a worker thread so the event loop keeps serving other dispatchers.

The Celery queue path exists (`roe.export_route` and the scheduled jobs) and
the worker is deployed, so moving the solve fully off-request is a
configuration change rather than a rewrite. Synchronous was chosen because it
gives the dispatcher a definitive result and a definitive timeout, which is
what Requirements 5.1 and 5.9 describe.

## 14. Orphaned runs are reaped

A run cannot outlive its 120-second ceiling, so any `in_progress` row older
than that is an orphan from a crashed or restarted worker. It is aborted
automatically — on API startup and whenever the concurrency guard is checked —
so a crash cannot permanently block optimisation (Requirement 12.7's guard
would otherwise reject every subsequent run).

## 15. Audit retention needs elevated privileges

`audit_log` is protected by `audit_no_update` / `audit_no_delete` rewrite rules
and the `roe_app` role holds INSERT + SELECT only. The 365-day purge therefore
runs as the table owner and disables the delete rule for the statement. Where
that privilege is not granted the job logs how many rows it *would* purge and
leaves them — retention is a floor, so keeping entries longer is always safe.

## 16. Map basemap

MapLibre GL renders routes, stops, and depots over whatever tile source is
configured. With none configured it uses a self-contained offline style, so the
plan is always visible without credentials or an outbound network call. A note
in the map corner tells the operator how to point at real tiles.

## 17. Route writes go through the ORM relationships

Persistence code links `Route → Stop → StopOrder` by appending to the
relationship collections rather than by setting foreign keys and issuing bulk
statements. Two reasons, both of which cost real defects before the rule was
adopted:

* A route built by setting `vehicle_id` and `route_id` comes back with its
  `stops` and `vehicle` collections *unloaded*. Reading either one later emits
  a lazy load, which on an async session is synchronous IO from the wrong
  context and raises `MissingGreenlet`.
* A bulk `DELETE FROM stop_orders` leaves already-loaded collections stale, so
  the unit of work later re-issues a delete for a row that is already gone, and
  the route's in-memory contents stop matching the database.

`_persist_schedule` therefore reconciles every stop's order links against its
plan — not only the links of stops it has just created. Reassigning an order
into a *co-located* stop grows an existing stop's plan, and skipping that case
counted the order in the route totals while never linking it to a stop.

## 18. A route keeps its planning day

Recomputing a route derives the planning day from the route's own schedule (the
earliest stop ETA) before falling back to the earliest delivery window and then
to the creation date. A route is already committed to a day; recalculating its
ETAs for new traffic, or reassigning an order onto it, must not silently move
the whole route to today.
