# Implementation Plan: Route Optimisation Engine (ROE)

## Overview

This plan breaks the ROE into nine implementation epics that build incrementally: database schema and shared types first, then core services (Route, Alert, Audit), integration adapters, the OR-Tools optimisation engine, the export and upload services, the REST/WebSocket API gateway, the React + MapLibre GL frontend, and finally RBAC, deployment configuration, and retention jobs. Every correctness property from the design document is covered by a Hypothesis property-based test sub-task placed close to the code it validates.

---

## Implementation status

Every task below is implemented and verified, with one deliberate exception:
**Requirement 17 (Access Control) was withdrawn by the product owner** — the
system ships with no login, no accounts and no credentials of any kind. Tasks
15.6, 15.7 and 15.8 are therefore not built, and 15.1, 15.5, 17.1 and 19.4 are
built without their access-control clauses. Properties 35 and 36 are withdrawn
with the requirement; the remaining 34 are covered by property tests.

Verified by: 98 backend tests (pytest, 100 Hypothesis examples per property),
42 frontend tests (vitest), 53 live API journey checks, 21 resilience checks,
23 browser UI checks and 11 accessibility checks — all green.

---

## Tasks

- [x] 1. Repository scaffold and shared foundations
  - Initialise a monorepo with two top-level workspaces: `backend/` (Python) and `frontend/` (TypeScript/React).
  - In `backend/`: create a `pyproject.toml` (Poetry) declaring dependencies — `fastapi`, `uvicorn`, `sqlalchemy[asyncio]`, `asyncpg`, `alembic`, `redis`, `pydantic`, `ortools`, `hypothesis`, `pytest`, `pytest-asyncio`, `httpx`, `celery[sqs]`, `boto3`, `openpyxl`, `python-multipart`.
  - In `frontend/`: bootstrap with Vite + React 18 + TypeScript; add `maplibre-gl`, `zustand`, `@tanstack/react-query`, `shadcn/ui`, `tailwindcss`, `recharts`, `@radix-ui/react-*`.
  - Create shared Pydantic models in `backend/app/schemas/` mirroring the Order, Vehicle, Route, Stop, Alert, AuditLog, and User schemas from the requirements data dictionary.
  - Define shared TypeScript interfaces in `frontend/src/types/` for the same entities.
  - _Requirements: All (foundational)_

- [x] 2. Database schema and migrations
  - [x] 2.1 Write Alembic initial migration creating all tables and indexes from the design SQL
    - Create `geopoint` composite type, `orders`, `vehicles`, `optimisation_runs`, `routes`, `stops`, `stop_orders`, `alerts`, `audit_log`, `users`, `geocoding_queue` tables.
    - Add all `CHECK` constraints, foreign keys, and `UNIQUE` constraints from the design.
    - Add the `dispatched_routes_locked` constraint (`status NOT IN ('dispatched','completed') OR locked = TRUE`).
    - Add `time_window_order` constraint on `orders`.
    - Apply all key indexes from the design.
    - _Requirements: 5.2, 5.3, 11.6, 16.1_

  - [x] 2.2 Apply immutability rules for audit_log
    - Create the `audit_no_update` and `audit_no_delete` PostgreSQL rules on `audit_log`.
    - Grant the application database role `INSERT` + `SELECT` only on `audit_log`; revoke `UPDATE`, `DELETE`, `TRUNCATE`.
    - Write a migration test asserting that an `UPDATE` or `DELETE` on `audit_log` is silently rejected.
    - _Requirements: 16.4_

  - [x]* 2.3 Write property test for audit log immutability (Property 33)
    - **Property 33: Audit log entries are immutable**
    - **Validates: Requirements 16.4**
    - Generate arbitrary audit log rows, attempt UPDATE and DELETE via SQLAlchemy, assert the row remains unchanged after each attempt.

- [x] 3. Audit Service
  - [x] 3.1 Implement AuditService with atomic transactional logging
    - Create `backend/app/services/audit_service.py`.
    - Implement `record_change(session, entity_id, entity_type, old_state, new_state, action, acting_user)` that performs an `INSERT` into `audit_log` within the caller's transaction.
    - If the audit `INSERT` raises an exception, re-raise so the caller's transaction rolls back.
    - Write failure to a separate `error_log` table (outside the failing transaction) using a second session.
    - _Requirements: 16.1, 16.4, 16.5_

  - [x]* 3.2 Write property test for audit log completeness (Property 32)
    - **Property 32: Audit log entries are created for every state change**
    - **Validates: Requirements 16.1**
    - For arbitrary Order, Vehicle, Route, and Alert state changes, assert that exactly one audit row exists per change with correct `entity_id`, `entity_type`, `old_state`, `new_state`, `acting_user`, and millisecond-precision UTC timestamp.

  - [x]* 3.3 Write property test for audit write failure rolls back entity change (Property 34)
    - **Property 34: Audit write failure prevents entity state change**
    - **Validates: Requirements 16.5**
    - Simulate audit `INSERT` failure (mock the session); assert the entity row is unchanged and an `error_log` entry is written.

- [x] 4. Alert Service
  - [x] 4.1 Implement AlertService core persistence and pub/sub broadcast
    - Create `backend/app/services/alert_service.py`.
    - Implement `raise_alert(session, alert_type, severity, entity_type, entity_id, message)` — persists `Alert` row, publishes to Redis channel `alerts:new`.
    - Implement `acknowledge_alert(session, alert_id, acting_user)` using the optimistic-locking `UPDATE ... WHERE acknowledged = false` pattern; return `False` if already acknowledged (409 semantics).
    - _Requirements: 14.1, 14.2, 14.3, 14.5, 14.6_

  - [x]* 4.2 Write property test for alert acknowledgement first-writer-wins (Property 29)
    - **Property 29: Alert acknowledgement is first-writer-wins**
    - **Validates: Requirements 14.5, 14.6**
    - Simulate concurrent acknowledgement of the same alert from two user IDs; assert only the first writer's `acknowledged_by` and `acknowledged_at` are stored and the second receives a conflict.

- [x] 5. Route Service — core lifecycle
  - [x] 5.1 Implement Route and Stop CRUD with status state machine
    - Create `backend/app/services/route_service.py`.
    - Implement `create_route`, `get_route`, `list_routes`, `update_route_status` enforcing the state machine `draft → approved → dispatched → completed`.
    - On transition to `dispatched`, set `locked = true` atomically within the same transaction.
    - Persist audit entries via `AuditService.record_change` for every status transition.
    - _Requirements: 11.6, 13.2, 16.1_

  - [x]* 5.2 Write property test for dispatched routes auto-locked (Property 26)
    - **Property 26: Dispatched routes are automatically locked**
    - **Validates: Requirements 11.6**
    - For any route transitioned to `dispatched`, assert `locked = true` immediately after the transition.

  - [x] 5.3 Implement route locking and unlocking with eligibility enforcement
    - Add `lock_route(session, route_id, acting_user)` — permits only `status IN ('draft', 'approved')`; raises 400 otherwise.
    - Add `unlock_route(session, route_id, acting_user)` — denies when `status IN ('dispatched', 'completed')`; raises 400 otherwise.
    - Persist audit entries for lock/unlock changes.
    - _Requirements: 11.1, 11.2, 11.4, 11.5_

  - [x]* 5.4 Write property test for route locking state machine (Property 24)
    - **Property 24: Route locking state machine**
    - **Validates: Requirements 11.1, 11.2, 11.4, 11.5**
    - For all combinations of `status` and attempted lock/unlock, assert the system permits or denies correctly.

  - [x] 5.5 Implement manual order reassignment with capacity and time-window validation
    - Add `reassign_order(session, order_id, source_route_id, dest_route_id, acting_user)`.
    - Check destination capacity (weight and, if defined, volume); raise 422 with capacity violation error if exceeded.
    - Resequence stops, recompute ETAs; if any ETA falls outside its time window, surface Late Delivery Alert and require dispatcher confirmation flag before committing.
    - Reject if `dest_route.locked = true`.
    - On recalculation failure or timeout (>10s), revert both routes to their prior state.
    - Persist audit entry capturing `acting_user`, `source_route_id`, `dest_route_id`, `order_id`, old and new states.
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7, 10.8_

  - [x]* 5.6 Write property test for reassignment recalculation consistency (Property 20)
    - **Property 20: Manual reassignment recalculation is consistent**
    - **Validates: Requirements 10.2**
    - For arbitrary valid reassignments, assert `total_weight_kg` equals sum of `cargo_weight_kg` for assigned orders and ETAs are monotonically increasing.

  - [x]* 5.7 Write property test for reassignment capacity rejection (Property 21)
    - **Property 21: Manual reassignment capacity rejection**
    - **Validates: Requirements 10.3, 10.4**
    - For any reassignment that would breach weight or volume capacity, assert rejection and both routes unchanged.

  - [x]* 5.8 Write property test for locked route reassignment rejection (Property 22)
    - **Property 22: Locked route reassignment rejection**
    - **Validates: Requirements 10.6**
    - For any reassignment targeting a locked destination route, assert rejection and both routes unchanged.

  - [x]* 5.9 Write property test for reassignment audit completeness (Property 23)
    - **Property 23: Manual reassignment audit completeness**
    - **Validates: Requirements 10.8**
    - For any successful reassignment, assert an audit entry exists with correct user ID, both route IDs, order ID, old/new states, and UTC timestamp.

  - [x] 5.10 Implement ETA recalculation and late delivery alert propagation
    - Add `recalculate_etas(session, route_id, updated_travel_times)` — recomputes all downstream ETAs cumulatively; raises Late Delivery Alert for any stop whose ETA now exceeds `time_window_end` by > 0 minutes.
    - Set `updated_at` on the route record and publish `route.updated` to Redis pub/sub.
    - _Requirements: 7.2, 7.3_

  - [x]* 5.11 Write property test for ETA recalculation downstream consistency (Property 16)
    - **Property 16: ETA recalculation propagates downstream consistently**
    - **Validates: Requirements 7.2**
    - For arbitrary travel-time updates, assert all downstream ETAs reflect cumulative travel time and no downstream ETA is stale.

  - [x]* 5.12 Write property test for late delivery alerts on time window violation (Property 17)
    - **Property 17: Late delivery alerts raised for all time window violations**
    - **Validates: Requirements 7.3, 14.2**
    - For any recalculated ETA exceeding `time_window_end` by > 0 minutes, assert a Late Delivery Alert with `severity = warning` is raised referencing that stop.

- [x] 6. Checkpoint — core services baseline
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Geocoding Service
  - [x] 7.1 Implement GeocodingService wrapping the Mapping Service API
    - Create `backend/app/services/geocoding_service.py`.
    - Implement `geocode_address(address)` — calls the Mapping Service `/geocode` endpoint; applies the confidence-threshold logic (≥0.8 → store directly, <0.8 or multiple candidates → store highest-confidence with `geocode_review = true`; no result → set `delivery_location = null`, raise Impossible Order Alert).
    - Implement `get_travel_time_matrix(waypoints)` — calls `/matrix`; returns `{(from_node, to_node): seconds}`.
    - On unavailability > 30s, switch to Redis-cached historical averages and set `data_quality_warning` on affected routes; retry every ≤60s.
    - _Requirements: 15.1, 15.2, 15.3, 15.4, 7.4, 7.5_

  - [x] 7.2 Implement geocoding retry queue processor
    - Persist failed geocoding requests to `geocoding_queue`.
    - Background worker reads queue, retries at ≤60s intervals, up to 5 attempts.
    - On exhaustion, raises Impossible Order Alert and prompts dispatcher to supply coordinates manually.
    - _Requirements: 15.6, 15.7_

  - [x] 7.3 Implement manual coordinate validation
    - Add `validate_manual_coordinates(lat, lon)` — accepts lat ∈ [−90, +90], lon ∈ [−180, +180]; returns descriptive error for out-of-range values.
    - _Requirements: 15.5_

  - [x]* 7.4 Write property test for geocoding confidence threshold logic (Property 30)
    - **Property 30: Geocoding confidence threshold logic**
    - **Validates: Requirements 15.2, 15.3**
    - For arbitrary confidence scores and candidate lists, assert the system stores directly (no flag) for score ≥ 0.8 single result, and stores with review flag otherwise.

  - [x]* 7.5 Write property test for coordinate validation (Property 31)
    - **Property 31: Coordinate validation rejects out-of-range values**
    - **Validates: Requirements 15.5**
    - For arbitrary `(lat, lon)` pairs, assert acceptance for valid ranges and descriptive rejection for out-of-range values.

- [x] 8. OMS Integration Adapter
  - [x] 8.1 Implement OMS event consumer and order ingestion
    - Create `backend/app/adapters/oms_adapter.py`.
    - Subscribe to the OMS message queue topic (RabbitMQ/SQS).
    - Implement `process_oms_event(event)` following the design pseudocode: validate required fields → deduplicate by `external_ref` → geocode if needed → persist with `source = OMS` within 30s SLA.
    - Reject events missing `delivery_location`/`cargo_weight_kg` or with non-positive `cargo_weight_kg`; raise Impossible Order Alert identifying the missing/invalid field by name.
    - Silently discard duplicate `external_ref` events.
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.6_

  - [x] 8.2 Implement OMS health watchdog and connectivity warning
    - Background task pings OMS queue every 15s.
    - If no successful ping for 60s, publish `connectivity.warning` to Redis pub/sub; auto-clear when connectivity is restored.
    - _Requirements: 1.5_

  - [x]* 8.3 Write property test for OMS event validation (Property 1)
    - **Property 1: OMS event validation rejects missing required fields**
    - **Validates: Requirements 1.2, 1.3**
    - For arbitrary OMS events where `delivery_location` is null or `cargo_weight_kg` is null/non-positive, assert no Order record is created and an Impossible Order Alert is raised naming the offending field.

  - [x]* 8.4 Write property test for OMS field mapping correctness (Property 2)
    - **Property 2: OMS field mapping preserves all data**
    - **Validates: Requirements 1.4**
    - For arbitrary valid OMS events, assert every field is correctly mapped in the persisted Order record and `external_ref` equals the OMS order identifier.

  - [x]* 8.5 Write property test for OMS deduplication idempotency (Property 3)
    - **Property 3: OMS deduplication is idempotent**
    - **Validates: Requirements 1.6**
    - Submit the same OMS event N times (N arbitrary ≥ 2); assert exactly one Order record with that `external_ref`.

- [x] 9. FMS Integration Adapter
  - [x] 9.1 Implement FMS event consumer and vehicle upsert logic
    - Create `backend/app/adapters/fms_adapter.py`.
    - Implement `process_fms_event(event)` following the design pseudocode: validate required fields → upsert by `external_ref` → cache updated vehicle in Redis.
    - Reject events missing `capacity_weight_kg` or `depot_location`; set `available = false`; notify dispatcher naming vehicle FMS ref and missing field.
    - Log and notify dispatcher when update references unknown `external_ref`.
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_

  - [x] 9.2 Implement FMS stale-data fallback and connectivity warning
    - On FMS unavailability, serve vehicle data from Redis cache.
    - Raise connectivity stale-data warning; auto-dismiss when FMS connectivity is restored.
    - _Requirements: 2.6_

  - [x]* 9.3 Write property test for FMS vehicle creation correctness (Property 4)
    - **Property 4: FMS vehicle creation sets correct source and external_ref**
    - **Validates: Requirements 2.2**
    - For arbitrary valid FMS new-registration events, assert `source = FMS` and `external_ref` equals the FMS reference.

  - [x]* 9.4 Write property test for FMS upsert non-duplication (Property 5)
    - **Property 5: FMS upsert never duplicates vehicles**
    - **Validates: Requirements 2.3**
    - For arbitrary FMS update events with an existing `external_ref`, assert exactly one Vehicle record with that ref and the record contains the updated data.

  - [x]* 9.5 Write property test for FMS validation of missing required fields (Property 6)
    - **Property 6: FMS validation rejects records with missing required fields**
    - **Validates: Requirements 2.5**
    - For FMS events missing `capacity_weight_kg` or `depot_location`, assert no Vehicle record is created/updated and the dispatcher is notified with the vehicle's FMS ref and field name.

- [x] 10. Manual Entry Service and forms backend
  - [x] 10.1 Implement manual Order and Vehicle creation endpoints
    - Create `backend/app/services/manual_entry_service.py`.
    - `create_order_manual(data, acting_user)` — validate fields (address 1–500 chars, `cargo_weight_kg` 0.01–99999.99, optional `cargo_volume_m3 ≥ 0`, optional ISO 8601 time window, `priority` enum); persist with `source = manual` within 2s SLA; return record with system-assigned `order_id`.
    - `create_vehicle_manual(data, acting_user)` — validate (`registration` 1–20 chars, `capacity_weight_kg` 0.01–99999.99, optional `capacity_volume_m3 > 0`, `depot_location` GeoPoint, `operating_hours_start/end` HH:MM); persist with `source = manual`.
    - On validation failure: return HTTP 422 with field-level error list; persist no data; preserve entered values in response for client-side restoration.
    - On persistence failure within 2s: return HTTP 500; preserve entered values.
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

  - [x] 10.2 Implement edit endpoint for manually entered records
    - Allow editing any manually entered Order or Vehicle before its associated route reaches `dispatched`.
    - Apply same validation rules as creation; persist audit entry for every edit.
    - _Requirements: 3.6_

  - [x]* 10.3 Write property test for manual form validation atomicity (Property 7)
    - **Property 7: Manual form validation rejects invalid submissions atomically**
    - **Validates: Requirements 3.3**
    - For arbitrary submissions with one or more invalid fields, assert no record persisted, all entered values returned in error response, field-level errors identify each invalid field.

  - [x]* 10.4 Write property test for manual form source assignment (Property 8)
    - **Property 8: Manual form submissions set source = manual**
    - **Validates: Requirements 3.4**
    - For arbitrary valid manual submissions, assert `source = manual` on every persisted record.

- [x] 11. Upload Service
  - [x] 11.1 Implement spreadsheet upload ingestion pipeline
    - Create `backend/app/services/upload_service.py`.
    - Accept CSV and XLSX files ≤ 50 MB and ≤ 10,000 rows.
    - Reject immediately (before row parsing) if size or row count exceeded; return descriptive error naming the exceeded limit.
    - Validate each row against Order or Vehicle schema; collect row-level errors `(row_number, column_name, reason)`.
    - Use a single PostgreSQL transaction per file: insert all valid rows with `source = spreadsheet`; rollback entirely on system error.
    - Return response: `{imported: N, skipped: M, errors: [...]}`.
    - Reject entirely (no records persisted) if zero valid rows.
    - _Requirements: 4.1, 4.2, 4.4, 4.5, 4.6, 4.7, 4.8_

  - [x] 11.2 Implement downloadable upload templates
    - Generate and serve downloadable CSV/XLSX templates for Order and Vehicle uploads with required column headings, data types, and one example row.
    - _Requirements: 4.3_

  - [x]* 11.3 Write property test for spreadsheet upload transactional atomicity (Property 9)
    - **Property 9: Spreadsheet upload transactional atomicity**
    - **Validates: Requirements 4.8**
    - Simulate a system error mid-import; assert zero records persisted from the upload.

  - [x]* 11.4 Write property test for spreadsheet row validation partitioning (Property 10)
    - **Property 10: Spreadsheet row validation correctly partitions valid and invalid rows**
    - **Validates: Requirements 4.4, 4.5**
    - For arbitrary mixes of valid and invalid rows, assert `imported == valid_count`, `skipped == invalid_count`, and each error entry names row number, column, and reason.

- [x] 12. Checkpoint — data ingest services complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 13. Optimisation Engine
  - [x] 13.1 Implement OptimisationRequest builder and OR-Tools CP-SAT solver
    - Create `backend/app/services/optimisation_engine.py`.
    - Implement `build_optimisation_request(session)` — fetches all `status = unassigned` orders and `available = true, capacity_weight_kg > 0` vehicles; excludes locked routes from solving.
    - Implement `solve(request: OptimisationRequest) -> OptimisationResult` using `ortools.sat.python.cp_model`.
    - Encode nodes (depot + order nodes per design), apply weight capacity, volume capacity, time-window, and operating-hours hard constraints.
    - Apply priority soft constraint via penalty weight so priority stops prefer earlier sequence positions.
    - Set multi-objective: primary minimise total distance, secondary minimise total time.
    - 110s solver time limit; on timeout return best incumbent or mark run `timed_out`.
    - Abort immediately if no available vehicles; return error.
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.9, 5.10, 5.11_

  - [x] 13.2 Implement pre-solve infeasibility detection and Impossible Order Alerts
    - Before the main solve, identify orders that cannot be assigned to any vehicle (weight > all vehicle capacities, time window impossible given depot operating hours); raise Impossible Order Alert for each; exclude from solve.
    - _Requirements: 5.7_

  - [x] 13.3 Implement optimisation run persistence and concurrency guard
    - Persist `optimisation_runs` row at run start.
    - Enforce single-run-at-a-time guard: reject new trigger if `status = in_progress` row exists; return 409 with "run already in progress" message.
    - On completion, persist draft Routes and Stops, set processed orders to `status = assigned`.
    - Publish `optimisation.complete` or `optimisation.failed` to Redis pub/sub.
    - Include `locked_excluded_count` in the completion payload.
    - _Requirements: 5.8, 5.9, 5.10, 12.1, 12.2, 12.3, 12.7_

  - [x] 13.4 Implement re-optimisation logic (locked route preservation)
    - On re-optimisation trigger: unassign orders from unlocked routes, pool with `status = unassigned` orders, re-solve.
    - Preserve locked routes exactly (stops, order assignments, ETAs unchanged).
    - Present diff of changed routes (changed orders, stop sequence, assigned driver) to dispatcher.
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.5_

  - [x] 13.5 Implement priority order flagging for re-optimisation
    - On new `priority = priority` order creation, within 60s flag all routes whose vehicle shares capacity or time-window overlap; notify dispatcher with affected route IDs.
    - _Requirements: 6.2, 6.3_

  - [x]* 13.6 Write property test for weight capacity invariant (Property 12)
    - **Property 12: Weight capacity invariant**
    - **Validates: Requirements 5.2**
    - For arbitrary optimisation inputs, assert no produced route has `total_weight_kg > vehicle.capacity_weight_kg`.

  - [x]* 13.7 Write property test for volume capacity invariant (Property 13)
    - **Property 13: Volume capacity invariant**
    - **Validates: Requirements 5.3**
    - For arbitrary optimisation inputs with vehicles having `capacity_volume_m3`, assert no produced route has `total_volume_m3 > vehicle.capacity_volume_m3`.

  - [x]* 13.8 Write property test for time window compliance invariant (Property 14)
    - **Property 14: Time window compliance invariant**
    - **Validates: Requirements 5.4**
    - For arbitrary inputs, assert every stop ETA is within `[time_window_start, time_window_end]` or an Impossible Order Alert has been raised for that order.

  - [x]* 13.9 Write property test for priority ordering invariant (Property 15)
    - **Property 15: Priority ordering invariant**
    - **Validates: Requirements 6.1**
    - For arbitrary routes with both priority and standard stops, assert no standard stop has a sequence position earlier than any priority stop unless a hard constraint requires it.

  - [x]* 13.10 Write property test for complete assignment coverage (Property 11)
    - **Property 11: Optimisation run produces complete assignment coverage**
    - **Validates: Requirements 5.1, 5.7**
    - For arbitrary order/vehicle sets, assert every order is either assigned to a route or has an Impossible Order Alert — none are silently unprocessed.

  - [x]* 13.11 Write property test for concurrent optimisation run rejection (Property 27)
    - **Property 27: Concurrent optimisation runs are rejected**
    - **Validates: Requirements 12.7**
    - While a run is in-progress, trigger a second run; assert rejection with "run already in progress" message.

  - [x]* 13.12 Write property test for locked routes preserved through re-optimisation (Property 25)
    - **Property 25: Locked routes are excluded from re-optimisation and preserved exactly**
    - **Validates: Requirements 11.3, 12.2**
    - For arbitrary re-optimisation runs, assert every locked route is byte-for-byte identical before and after; assert `locked_excluded_count` in result is accurate.

- [x] 14. Export Service
  - [x] 14.1 Implement Delivery Platform export with exponential back-off retry
    - Create `backend/app/services/export_service.py`.
    - On route approval, enqueue export job.
    - Implement retry sequence: attempt 0 (0s), attempt 1 (5s), attempt 2 (10s), attempt 3 (20s); total 3 retries beyond initial.
    - On all retries exhausted: leave route `status = approved`, raise export failure alert, mark for manual re-trigger.
    - Implement `manual_export(route_id, acting_user)` — cancels any in-progress retry sequence before starting fresh.
    - On Delivery Platform acknowledgement, update route `status = dispatched` within 5s.
    - _Requirements: 13.1, 13.2, 13.3, 13.4, 13.5_

  - [x]* 14.2 Write property test for export retry exponential back-off (Property 28)
    - **Property 28: Export retry respects exponential back-off**
    - **Validates: Requirements 13.3**
    - For simulated delivery platform failures, assert retry count ≤ 3, delays are 5s → 10s → 20s, and no interval exceeds 20s.

- [x] 15. API Gateway / BFF
  - [x] 15.1 Implement REST API endpoints ~~with JWT authentication middleware~~ *(withdrawn — the product owner asked for a system with no login details; see docs/DECISIONS.md §6)*
    - Create `backend/app/api/` with FastAPI routers for all endpoints defined in the design.
    - Implement JWT validation middleware: verify signature, expiry, issuer; extract `role` claim.
    - Implement `@require_role([...])` decorator; return HTTP 403 with `{"error": "forbidden", "message": "..."}` on role mismatch.
    - Log all access denials via `AuditService` with `action = 'access.denied'`, user identity, attempted action, and UTC timestamp.
    - Return HTTP 401 for missing/invalid authentication credentials on any endpoint.
    - _Requirements: 17.1, 17.2, 17.3, 17.4, 17.5_

  - [x] 15.2 Implement WebSocket hub for real-time dispatcher events
    - Implement WebSocket connection manager in the API Gateway.
    - Subscribe to Redis pub/sub channels and fan out events to connected dispatcher clients.
    - Broadcast: `route.updated`, `alert.raised`, `alert.acknowledged`, `optimisation.progress` (≤5s intervals), `optimisation.complete`, `optimisation.failed`, `connectivity.warning`, `connectivity.restored`.
    - _Requirements: 8.6, 12.6, 14.4, 18.4, 18.6_

  - [x] 15.3 Implement optimisation and re-optimisation API endpoints
    - Wire `POST /api/v1/optimise` to `OptimisationEngine.solve` via async worker queue.
    - Implement progress broadcast: publish `optimisation.progress` to Redis every ≤5s during solve.
    - Return 409 if run already in progress.
    - _Requirements: 5.1, 5.9, 12.7, 18.6_

  - [x] 15.4 Implement alert acknowledgement and listing endpoints
    - `GET /api/v1/alerts` — return all unacknowledged alerts plus history.
    - `PATCH /api/v1/alerts/{id}/acknowledge` — call `AlertService.acknowledge_alert`; return 409 on conflict.
    - _Requirements: 14.4, 14.5, 14.6_

  - [x] 15.5 Implement audit log query endpoint ~~(Admin only)~~ — open, like every other endpoint
    - `GET /api/v1/audit` with filter params: `entity_type`, `entity_id`, `from_date`, `to_date`, `acting_user`.
    - Enforce `@require_role(['administrator'])`.
    - Backed by composite index query; assert response within 5s for 365-day window.
    - _Requirements: 16.2, 16.6, 17.3_

  - [ ] 15.6 ~~Implement user management endpoints (Admin only)~~ *(withdrawn — the product owner asked for a system with no login details; see docs/DECISIONS.md §6)*
    - `POST /api/v1/users` — create user (administrator only).
    - `PATCH /api/v1/users/{id}/role` — update role; the change takes effect within 60s via short-lived JWT (15-min expiry).
    - _Requirements: 17.1, 17.6_

  - [ ]* 15.7 ~~Write property test for RBAC out-of-role denial (Property 35)~~ *(withdrawn — the product owner asked for a system with no login details; see docs/DECISIONS.md §6)*
    - **Property 35: RBAC denies out-of-role actions for all users**
    - **Validates: Requirements 17.2, 17.3, 17.5**
    - For arbitrary users with dispatcher or administrator roles, assert every out-of-role action returns HTTP 403 and an audit denial entry is created.

  - [ ]* 15.8 ~~Write property test for unauthenticated request denial (Property 36)~~ — inverted: `test_every_endpoint_is_open_without_credentials` asserts every endpoint answers *without* credentials
    - **Property 36: All API endpoints deny unauthenticated requests**
    - **Validates: Requirements 17.4**
    - For every API endpoint, assert requests without valid JWT receive HTTP 401 and access is denied.

- [x] 16. Checkpoint — backend complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 17. React frontend — map and route views
  - [x] 17.1 Implement App shell ~~with AuthGuard~~, Layout, and WebSocket connection *(withdrawn — the product owner asked for a system with no login details; see docs/DECISIONS.md §6)*
    - Bootstrap the React app with AuthGuard (JWT validation, role extraction from claims).
    - Implement `Layout` with persistent `AlertPanel`, `ConnectivityBanner`, and main content area.
    - Implement WebSocket client with reconnect wrapper; wire incoming events to Zustand store.
    - _Requirements: 8.1, 14.4, 17.4_

  - [x] 17.2 Implement MapView with route polylines, stop markers, and depot markers
    - Render `RoutePolylineLayer` — one polyline per vehicle with deterministic colour derived from `vehicle_id` hash.
    - Render `StopMarkerLayer` — cluster below zoom 12, individual markers above; hover tooltip shows sequence number, address, and ETA.
    - Render `DepotMarkerLayer` — distinct symbol layer for each vehicle's depot.
    - Wire map click via `queryRenderedFeatures` to route/stop selection.
    - Update map within 5s of any `route.updated` WebSocket event by invalidating TanStack Query cache.
    - Show error message + manual refresh action if map data fails to load.
    - _Requirements: 8.1, 8.2, 8.3, 8.6, 8.7_

  - [x] 17.3 Implement RouteSummaryPanel with load utilisation indicators
    - List all routes with: vehicle name, total stop count, `total_distance_km` (2 d.p.), `total_duration_min` (whole number), weight load utilisation % (1 d.p.).
    - Render volume utilisation % (1 d.p.) where `capacity_volume_m3` is defined.
    - Apply load indicators: green progress bar < 90%; amber + warning icon 90%–<100%; red + critical icon ≥ 100%.
    - Display "N/A" and data-quality indicator for missing/invalid weight or volume data.
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5_

  - [x] 17.4 Implement RouteDetailPanel and StopDetailPanel with selection behaviour
    - On route selection: highlight polyline, show Route summary panel (vehicle name, `total_distance_km`, `total_duration_min`, `total_weight_kg`, load utilisation %).
    - On stop selection: show Stop detail panel (order IDs, total cargo weight, time window, current ETA).
    - Add `RouteActions` component (lock/unlock, approve, export buttons) calling corresponding API endpoints.
    - _Requirements: 8.4, 8.5_

  - [x]* 17.5 Write property test for route summary panel metric accuracy (Property 18)
    - **Property 18: Route summary panel displays accurate metrics**
    - **Validates: Requirements 8.4, 9.1**
    - For arbitrary route data, assert displayed `total_distance_km` (2 d.p.), `total_duration_min` (whole number), and utilisation % (1 d.p.) match stored values exactly.

  - [x]* 17.6 Write property test for capacity threshold indicator correctness (Property 19)
    - **Property 19: Capacity threshold indicators are correct for all utilisation values**
    - **Validates: Requirements 9.2, 9.3, 14.1**
    - For arbitrary weight/capacity pairs, assert the correct indicator variant (none / warning / critical) is rendered and an Overload Alert is raised at ≥ 100%.

- [x] 18. React frontend — drag-and-drop reassignment and manual entry
  - [x] 18.1 Implement drag-and-drop order reassignment in StopList
    - Implement pointer-event drag-and-drop on `StopList` (not on map canvas).
    - Call `POST /api/v1/orders/reassign` on drop.
    - On capacity violation response: show capacity violation error; leave both routes unchanged.
    - On Late Delivery Alert response: show confirmation dialog listing affected stops; commit only on dispatcher confirm.
    - On locked route rejection: show locked route error.
    - Zoom map to affected stops on confirmation.
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6_

  - [x] 18.2 Implement ManualEntryDrawer with OrderForm and VehicleForm
    - Client-side validation matching server rules (field presence, range checks).
    - On server 422 response: map `fields` array to per-field error messages; preserve all entered values in local state.
    - On server 500: show error message; preserve entered values.
    - Show success message with system-assigned identifier on persisted record.
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

  - [x] 18.3 Implement UploadDrawer with FileDropzone and ValidationResultTable
    - File dropzone enforcing ≤ 50 MB and ≤ 10,000 rows; show immediate rejection with exceeded-limit message.
    - Display `ValidationResultTable` with row-level error list (row number, column, reason).
    - Show imported/skipped counts after successful upload.
    - Provide template download links for both upload types.
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7_

- [x] 19. React frontend — alert panel, optimisation controls, and audit log
  - [x] 19.1 Implement persistent AlertPanel with WebSocket-driven updates
    - Render all unacknowledged alerts with: alert type, severity, entity IDs, creation timestamp.
    - On `alert.raised` WebSocket event: prepend to panel.
    - On `alert.acknowledged` WebSocket event: remove from unacknowledged list for all dispatcher clients in session.
    - Dispatcher acknowledge action: call `PATCH /api/v1/alerts/{id}/acknowledge`; handle 409 gracefully (show "already acknowledged").
    - _Requirements: 14.4, 14.5, 14.6_

  - [x] 19.2 Implement OptimiseButton / ReoptimiseButton with progress bar
    - Show progress indicator updating ≤5s from `optimisation.progress` WebSocket events.
    - Disable button while run in progress; show "run already in progress" on 409 response.
    - On `optimisation.complete`: refresh route list, highlight changed routes with diff indicator.
    - On `optimisation.failed` / timeout: surface error message to dispatcher.
    - Notify dispatcher when unassigned orders are pending with one-click re-optimise action.
    - _Requirements: 5.8, 5.9, 12.4, 12.5, 12.6, 18.4, 18.6_

  - [x] 19.3 Implement AuditLogModal (Administrator only)
    - Render tabular audit log with filter controls: `entity_type`, `entity_id`, `from_date`, `to_date`, `acting_user`.
    - ~~Gate behind `role = administrator` check; hide from dispatcher UI.~~ Open to every user — there are no roles.
    - _Requirements: 16.6, 17.3_

  - [x] 19.4 Implement ConnectivityBanner for OMS, FMS, and Mapping Service warnings
    - Show persistent banner when `connectivity.warning` WebSocket event is received.
    - Auto-dismiss on `connectivity.restored` event.
    - Display stale-data warning indicators on affected routes when Mapping Service is unavailable.
    - _Requirements: 1.5, 2.6, 7.4_

  - [x] 19.5 Implement loading indicators for slow operations
    - Show loading indicator when any user-facing operation exceeds 5s.
    - _Requirements: 18.4_

- [x] 20. Checkpoint — frontend complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 21. Retention jobs and system health
  - [x] 21.1 Implement audit log and route retention scheduled jobs
    - Implement a scheduled cleanup job that purges `audit_log` entries older than 365 days.
    - Implement a scheduled cleanup job that purges completed `routes` (and their stops and order snapshots) older than 365 days from route completion timestamp.
    - Implement a scheduled cleanup job that purges `alerts` older than 90 days from `raised_at`.
    - _Requirements: 14.7, 16.2, 16.3_

  - [x] 21.2 Implement performance monitoring and timeout enforcement
    - Enforce 120s timeout on all Optimisation Run and re-optimisation operations; abort and preserve last stable state on breach.
    - Enforce 3s timeout on manual reassignment recalculations; revert both routes on breach.
    - Log all timeout events.
    - _Requirements: 5.9, 10.7, 12.5, 18.2, 18.5_

- [x] 22. Infrastructure and deployment configuration
  - [x] 22.1 Write Docker Compose configuration for local development
    - Define services: `postgres` (PostgreSQL 15), `redis` (Redis 7), `rabbitmq` (or mock SQS), `api` (FastAPI backend), `worker` (optimisation worker), `frontend` (Vite dev server).
    - Include environment variable files (`.env.example`) and volume mounts for database persistence.
    - _Requirements: All (infrastructure)_

  - [x] 22.2 Write production deployment configuration (Kubernetes or ECS task definitions)
    - Define API service (stateless, horizontal scaling), optimisation worker (queue-based, horizontal), PostgreSQL (managed RDS), Redis (managed cluster).
    - Configure health checks, resource limits, and environment variable injection from secrets manager.
    - _Requirements: 18.1, 18.2_

- [x] 23. Final checkpoint — full system integration
  - Run the full test suite (`pytest --tb=short` for backend, `vitest --run` for frontend).
  - Verify all 36 property tests are present and passing.
  - Verify all 18 requirements are covered by at least one task.
  - Ensure all tests pass, ask the user if questions arise.

---

## Notes

- Sub-tasks marked with `*` are optional and can be skipped for a faster MVP — they are property-based or unit tests, not core implementation.
- Each task references specific requirements for full traceability back to the 18 requirements.
- All 36 correctness properties from the design document are covered by dedicated Hypothesis property test sub-tasks.
- Property tests use the tag format: `# Feature: route-optimisation-engine, Property {N}: {property_text}`.
- Every property test runs a minimum of 100 iterations with randomly generated inputs.
- Backend language: Python 3.11+ (FastAPI, OR-Tools, Hypothesis, SQLAlchemy async, Alembic).
- Frontend language: TypeScript (React 18, MapLibre GL JS, Zustand, TanStack Query, shadcn/ui).
- Checkpoints at tasks 6, 12, 16, 20, and 23 ensure incremental validation throughout the build.

---

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["2.1"] },
    { "id": 1, "tasks": ["2.2", "3.1"] },
    { "id": 2, "tasks": ["2.3", "3.2", "3.3", "4.1"] },
    { "id": 3, "tasks": ["4.2", "5.1"] },
    { "id": 4, "tasks": ["5.2", "5.3"] },
    { "id": 5, "tasks": ["5.4", "5.5"] },
    { "id": 6, "tasks": ["5.6", "5.7", "5.8", "5.9", "5.10", "7.1"] },
    { "id": 7, "tasks": ["5.11", "5.12", "7.2", "7.3", "8.1"] },
    { "id": 8, "tasks": ["7.4", "7.5", "8.2", "8.3", "9.1", "10.1"] },
    { "id": 9, "tasks": ["8.4", "8.5", "9.2", "9.3", "10.2", "11.1"] },
    { "id": 10, "tasks": ["9.4", "9.5", "10.3", "10.4", "11.2", "13.1"] },
    { "id": 11, "tasks": ["11.3", "11.4", "13.2"] },
    { "id": 12, "tasks": ["13.3", "13.4"] },
    { "id": 13, "tasks": ["13.5", "13.6", "13.7", "13.8", "14.1"] },
    { "id": 14, "tasks": ["13.9", "13.10", "13.11", "13.12", "14.2", "15.1"] },
    { "id": 15, "tasks": ["15.2", "15.3"] },
    { "id": 16, "tasks": ["15.4", "15.5", "15.6"] },
    { "id": 17, "tasks": ["15.7", "15.8", "17.1"] },
    { "id": 18, "tasks": ["17.2", "17.3"] },
    { "id": 19, "tasks": ["17.4", "17.5", "17.6", "18.1"] },
    { "id": 20, "tasks": ["18.2", "18.3", "19.1"] },
    { "id": 21, "tasks": ["19.2", "19.3", "19.4", "19.5"] },
    { "id": 22, "tasks": ["21.1", "21.2"] },
    { "id": 23, "tasks": ["22.1", "22.2"] }
  ]
}
```
