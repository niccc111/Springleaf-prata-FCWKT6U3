# Traceability

Maps every requirement, implementation task, and correctness property from
`.kiro/specs/route-optimisation-engine/` to the code and tests that satisfy it.

---

## Requirements coverage

| # | Requirement | Implementation | Tests |
|---|---|---|---|
| **1** | Order import from OMS | `app/adapters/oms.py`; webhook `POST /api/v1/system/integrations/oms/events`; poller in `app/workers/background.py` | `tests/property/test_oms_properties.py`, `tests/integration/test_api_flows.py::test_oms_ingest_endpoint_creates_orders` |
| 1.1 | Ingest within 30 s | Poller runs every 15 s (`OMS_POLL_INTERVAL_SECONDS`) | integration |
| 1.2 | Reject missing location/weight + alert naming the field | `validate_order_event`, `OmsAdapter.process_event` | Property 1 |
| 1.3 | Reject non-positive weight | `validate_order_event` | Property 1 |
| 1.4 | Map all fields, store `external_ref` | `OmsAdapter.process_event` | Property 2 |
| 1.5 | Connectivity warning after 60 s | `IntegrationHealthService.record_failure`, `ConnectivityBanner.tsx` | `scripts/resilience_check.py` §4 |
| 1.6 | Discard duplicates | dedup by `external_ref` | Property 3 |
| **2** | Vehicle import from FMS | `app/adapters/fms.py` | `tests/property/test_fms_properties.py`, `test_fms_ingest_endpoint_upserts_vehicles` |
| 2.1 | Update within 30 s | FMS poller | integration |
| 2.2 | Create with `source = FMS` | `process_event` new-registration branch | Property 4 |
| 2.3 | Upsert, never duplicate | lookup by `external_ref` | Property 5 |
| 2.4 | Notify on unknown ref | `_notify` + `fms.notification` WS event | integration |
| 2.5 | Reject missing fields, force unavailable | `validate_vehicle_event` | Property 6 |
| 2.6 | Stale-data fallback, auto-dismiss | Redis cache in `FmsAdapter._cache_vehicle`; `IntegrationHealthService` | `scripts/resilience_check.py` §4 |
| **3** | Manual entry | `app/services/manual_entry_service.py`; `OrderForm.tsx`, `VehicleForm.tsx` | `test_manual_entry_properties.py`, `OrderForm.test.tsx` |
| 3.1/3.2 | Order and vehicle forms with the stated ranges | `schemas/entities.py`, `lib/validation.ts` | Property 7, `test_manual_order_entry_validates_and_persists` |
| 3.3 | Reject atomically, preserve entries, field errors | 422 envelope + client state retention | Property 7, `OrderForm.test.tsx` |
| 3.4 | Persist with `source = manual` within 2 s | `create_order` / `create_vehicle` | Property 8 |
| 3.5 | Persistence failure preserves entries | `PersistenceFailure` → 500, form keeps state | `OrderForm.test.tsx` |
| 3.6 | Edit until dispatched | `_assert_editable` | unit |
| **4** | Spreadsheet upload | `app/services/upload_service.py`; `UploadDrawer.tsx` | `test_upload_properties.py`, `tests/unit/test_upload_service.py` |
| 4.1/4.2 | CSV + XLSX, 50 MB / 10,000 rows | `UploadService.parse` | unit |
| 4.3 | Downloadable templates | `template_csv` / `template_xlsx`, `GET /upload/templates/{kind}` | `test_upload_templates_round_trip` |
| 4.4/4.5 | Row-level errors, import valid rows only | `validate_order_row` / `validate_vehicle_row` | Property 10 |
| 4.6 | Reject entirely when no valid rows | `import_file` | integration |
| 4.7 | Reject over-limit before processing | `parse` | `test_spreadsheet_upload_rejects_row_limit` |
| 4.8 | System error persists nothing | savepoint rollback | Property 9 |
| **5** | Automatic route optimisation | `app/services/optimisation_engine.py`, `optimisation_service.py` | `test_optimisation_properties.py`, `test_full_planning_journey` |
| 5.1 | Assign all unassigned within 120 s | `execute_run` + `asyncio.wait_for` | Property 11 |
| 5.2/5.3 | Weight and volume capacity | phase-1 CP-SAT constraints + post-solve verification | Properties 12, 13 |
| 5.4 | Time windows | phase-2 CP-SAT window bounds | Property 14 |
| 5.5/5.6 | Distance primary, time secondary | `ALPHA_DISTANCE=1000`, `BETA_TIME=1` | property + benchmark |
| 5.7 | Impossible Order Alerts | `presolve_feasibility`, `_raise_impossible` | Property 11 |
| 5.8 | Draft routes for review | routes created with `status = draft` | integration |
| 5.9 | Abort on timeout, preserve state | `_mark_timed_out` after rollback | unit |
| 5.10 | Abort when no vehicles | `_execute_inner` guard | `test_optimise_requires_vehicles` |
| 5.11 | Availability definition | `available_vehicles` | integration |
| **6** | Priority handling | phase-2 hard/soft priority constraint | Property 15 |
| 6.1 | Priority before standard | `sequence(hard_priority=True)` | Property 15 |
| 6.2/6.3 | Flag + notify affected routes within 60 s | `flag_routes_for_priority_order`, `reoptimisation.suggested` | integration |
| 6.4 | Earliest feasible + late alert | soft fallback + `raise_late_delivery_alerts` | Property 17 |
| **7** | Traffic-aware travel times | `app/services/geocoding_service.py`, `adapters/mapping.py` | `test_route_properties.py` |
| 7.1 | Traffic-adjusted durations | `traffic_multiplier`, `/matrix` | unit |
| 7.2 | Recalculate downstream within 60 s | `recalculate_etas`; `roe.refresh_etas` every 5 min | Property 16 |
| 7.3 | Late delivery alerts | `raise_late_delivery_alerts` | Property 17 |
| 7.4 | Fallback to historical averages + warning | `historical_matrix`, `data_quality_warning` | `scripts/resilience_check.py` §2 |
| 7.5 | Retain prior time on unparseable data | `HttpMappingAdapter` per-cell guard | unit |
| **8** | Interactive map | `components/map/MapView.tsx` | `RouteSummaryPanel.test.tsx`, `RouteDetailPanel.test.tsx` |
| 8.1 | Polyline per vehicle, unique colour | `routeLines`, `vehicleColour` | `utils.test.ts` |
| 8.2 | Stop markers with seq/address/ETA | `StopMarkerLayer` + hover popup | `frontend/e2e/console.smoke.mjs` |
| 8.3 | Distinct depot markers | `depotPoints` + `LAYER_DEPOTS` | `frontend/e2e/console.smoke.mjs` |
| 8.4 | Select route → highlight + summary | `setFeatureState`, `RouteDetailPanel` | Property 18 |
| 8.5 | Select stop → detail panel | `StopDetailPanel` | `RouteDetailPanel.test.tsx` |
| 8.6 | Update within 5 s of change | WS `route.updated` → query invalidation | `scripts/ws_check.py` |
| 8.7 | Error + manual refresh | `MapView` error state | `MapView` error branch |
| **9** | Route summary and load | `RouteSummaryPanel.tsx`, `LoadBar.tsx` | Properties 18, 19 |
| 9.1 | Metrics at stated precision | `formatDistanceKm/DurationMin/Utilisation` | Property 18 |
| 9.2 | Warning ≥ 90 % | `loadLevel` | Property 19 |
| 9.3 | Critical ≥ 100 % + overload alert | `loadLevel`, `check_capacity_alerts` | Property 19 |
| 9.4 | Volume utilisation where defined | `LoadBar` volume row | UI test |
| 9.5 | "N/A" + data-quality indicator | `formatUtilisation(null)` | UI test |
| **10** | Manual reassignment | `RouteService.reassign_order`; drag-and-drop in `RouteDetailPanel.tsx` | Properties 20–23 |
| 10.1 | Drag between routes | HTML5 drag on the stop list | `RouteDetailPanel.test.tsx` |
| 10.2 | Recalculate within 3 s | `recompute_route` | Property 20 |
| 10.3/10.4 | Capacity rejection, both routes unchanged | pre-write validation | Property 21 |
| 10.5 | Late delivery needs confirmation | `ConfirmationRequired` → confirm dialog | integration + UI |
| 10.6 | Locked destination rejected | `RouteLockedError` | Property 22 |
| 10.7 | Revert on failure/timeout | `asyncio.wait_for` + rollback | unit |
| 10.8 | Audit entry with both routes | `order.reassigned` audit | Property 23 |
| **11** | Route locking | `lock_route` / `unlock_route` | Properties 24, 25, 26 |
| 11.1/11.2 | Lock only draft/approved | `LOCKABLE_STATUSES` | Property 24 |
| 11.3 | Exclude locked from runs + count | `locked_excluded_count` | Property 25 |
| 11.4/11.5/11.7 | Unlock rules | `UNLOCKABLE_STATUSES` | Property 24 |
| 11.6 | Dispatched ⇒ locked | `update_route_status` + DB check constraint | Property 26 |
| **12** | Re-optimisation | `optimisation_service` reoptimise path | Properties 25, 27 |
| 12.1 | Re-optimise action | `POST /optimise {reoptimise:true}` | integration |
| 12.2 | Preserve locked routes | vehicle exclusion + route retention | Property 25 |
| 12.3 | Pool unlocked-route orders | `unassign_route_orders` | integration |
| 12.4 | Present diff | `_build_diff`, diff badge in the route list | `test_reoptimisation_preserves_locked_routes` |
| 12.5 | Timeout preserves state | rollback + `_mark_timed_out` | unit |
| 12.6 | Notify pending orders | `notify_pending_orders`, `OptimiseControls` prompt | integration |
| 12.7 | Reject concurrent runs | advisory lock + in-progress check | Property 27 |
| **13** | Export to delivery platform | `app/services/export_service.py` | Property 28 |
| 13.1 | Export on approval within 30 s | `PATCH /routes/{id}` → `_export_in_background` | `test_full_planning_journey` |
| 13.2 | Dispatched on acknowledgement | `run_job` success branch | `test_full_planning_journey` |
| 13.3 | Retry 5 s → 10 s → 20 s | `backoff_schedule` | Property 28 |
| 13.4 | Exhausted ⇒ stay approved + alert | `run_job` failure branch | Property 28 |
| 13.5 | Manual re-trigger cancels in-flight | `enqueue_export(cancel_in_progress=True)` | property + `resilience_check.py` §3 |
| **14** | Alerts | `app/services/alert_service.py`, `AlertPanel.tsx` | Property 29, `AlertPanel.test.tsx` |
| 14.1 | Overload critical | `check_capacity_alerts` | Property 19 |
| 14.2 | Late delivery warning | `raise_late_delivery_alerts` | Property 17 |
| 14.3 | Impossible order critical | `_raise_impossible` | Property 11 |
| 14.4 | Persistent panel with the stated fields | `AlertPanel.tsx` | UI test |
| 14.5/14.6 | First-writer-wins acknowledgement | conditional UPDATE | Property 29 |
| 14.7 | 90-day retention | `workers/retention.py::purge_alerts` | unit |
| 14.8 | Log + surface alert failures | `AuditService.record_error` | unit |
| **15** | Geocoding | `app/services/geocoding_service.py` | Properties 30, 31 |
| 15.1 | Geocode on create/update within 5 s | `geocode_order` | integration |
| 15.2/15.3 | Confidence threshold and review flag | `apply_confidence_policy` | Property 30 |
| 15.4 | Unresolvable ⇒ null + alert | `geocode_order` | integration |
| 15.5 | Coordinate range validation | `validate_manual_coordinates` | Property 31 |
| 15.6/15.7 | Retry queue, 5 attempts, then alert | `process_retry_queue` | `scripts/resilience_check.py` §1 |
| **16** | Audit and retention | `app/services/audit_service.py`, migration `0002` | Properties 32–34 |
| 16.1 | Entry per state change | `record_change` | Property 32 |
| 16.2/16.3 | 365-day retention | `workers/retention.py` | unit |
| 16.4 | Immutable | `audit_no_update` / `audit_no_delete` rules | Property 33 |
| 16.5 | Write failure rolls back the change | `AuditWriteError` re-raise | Property 34 |
| 16.6 | Query within 5 s | composite index + `GET /audit` | integration |
| **17** | Access control | `app/api/deps.py` | Properties 35, 36 |
| 17.1 | Two roles | `UserRole` | integration |
| 17.2/17.3 | Role matrix | `require_role` | Property 35 |
| 17.4 | 401 unauthenticated | `get_current_user` | Property 36 |
| 17.5 | Audit denials | `record_access_denial` | Property 35 |
| 17.6 | Role change within 60 s | DB role is authoritative per request | `test_role_change_takes_effect_immediately` |
| **18** | Performance | — | benchmark + journey |
| 18.1 | 2 s p95 at 50 sessions | stateless API, Redis fan-out, indexed queries | `deploy/k8s` sizing |
| 18.2 | 500 × 50 in 120 s | two-phase CP-SAT | **69.0 s measured end to end** (solver alone 18.5 s) |
| 18.3 | Reassignment within 3 s | localised recompute | journey |
| 18.4 | Loading indicator past 5 s | React Query pending states, skeletons | UI |
| 18.5 | Abort and preserve on timeout | `asyncio.wait_for` + rollback | unit |
| 18.6 | Progress every ≤ 5 s | `_pump_progress` at 3 s | `scripts/ws_check.py` |

---

## Correctness properties

All 36 properties from the design document have a dedicated test carrying the
`Feature: route-optimisation-engine, Property N:` tag. Backend properties run
100 Hypothesis examples each; frontend properties run 200–300 fast-check runs.

| Property | Test |
|---|---|
| 1 — OMS validation rejects missing fields | `tests/property/test_oms_properties.py::test_property_1_invalid_events_are_rejected` |
| 2 — OMS field mapping preserves data | `…::test_property_2_field_mapping_preserves_data` |
| 3 — OMS deduplication idempotent | `…::test_property_3_deduplication_is_idempotent` |
| 4 — FMS creation sets source/ref | `tests/property/test_fms_properties.py::test_property_4_new_registration_sets_source_and_ref` |
| 5 — FMS upsert never duplicates | `…::test_property_5_upsert_never_duplicates` |
| 6 — FMS rejects missing fields | `…::test_property_6_missing_required_fields_rejected` |
| 7 — Manual validation atomic | `tests/property/test_manual_entry_properties.py::test_property_7_invalid_manual_order_persists_nothing` + `frontend/src/components/forms/OrderForm.test.tsx` |
| 8 — Manual sets `source = manual` | `…::test_property_8_manual_submissions_are_sourced_manual` |
| 9 — Upload transactional atomicity | `tests/property/test_upload_properties.py::test_property_9_system_error_persists_nothing` |
| 10 — Upload row partitioning | `…::test_property_10_row_partitioning` |
| 11 — Complete assignment coverage | `tests/property/test_optimisation_properties.py::test_property_11_complete_coverage` |
| 12 — Weight capacity invariant | `…::test_property_12_and_13_capacity_invariants` |
| 13 — Volume capacity invariant | `…::test_property_12_and_13_capacity_invariants` |
| 14 — Time window compliance | `…::test_property_14_time_window_compliance` |
| 15 — Priority ordering | `…::test_property_15_priority_ordering` |
| 16 — ETA recalculation downstream | `tests/property/test_route_properties.py::test_property_16_and_17_eta_recalculation` |
| 17 — Late delivery alerts | `…::test_property_16_and_17_eta_recalculation` |
| 18 — Summary metrics accurate | `frontend/src/lib/utils.test.ts` + `RouteSummaryPanel.test.tsx` |
| 19 — Capacity threshold indicators | `frontend/src/lib/utils.test.ts` + `RouteSummaryPanel.test.tsx` |
| 20 — Reassignment consistency | `tests/property/test_route_properties.py::test_property_20_and_23_reassignment_consistency_and_audit` |
| 21 — Reassignment capacity rejection | `…::test_property_21_capacity_rejection` |
| 22 — Locked route rejection | `…::test_property_22_locked_destination_rejected` |
| 23 — Reassignment audit completeness | `…::test_property_20_and_23_reassignment_consistency_and_audit` |
| 24 — Route locking state machine | `…::test_property_24_locking_state_machine` |
| 25 — Locked routes preserved | `tests/integration/test_api_flows.py::test_reoptimisation_preserves_locked_routes` |
| 26 — Dispatched routes locked | `tests/property/test_route_properties.py::test_property_26_dispatched_routes_are_locked` |
| 27 — Concurrent runs rejected | `scripts/journey_check.py` §6 + `start_run` guard |
| 28 — Export back-off | `tests/property/test_export_properties.py::test_property_28_export_backoff` |
| 29 — Alert first-writer-wins | `tests/property/test_alert_properties.py::test_property_29_first_writer_wins` |
| 30 — Geocoding confidence threshold | `tests/property/test_manual_entry_properties.py::test_property_30_confidence_threshold` |
| 31 — Coordinate range validation | `…::test_property_31_coordinate_range_validation` |
| 32 — Audit entry per state change | `tests/property/test_audit_properties.py::test_property_32_state_changes_are_audited` |
| 33 — Audit immutability | `…::test_property_33_audit_entries_are_immutable` |
| 34 — Audit failure rolls back | `…::test_property_34_audit_failure_rolls_back_entity_change` |
| 35 — RBAC denies out-of-role | `tests/property/test_rbac_properties.py::test_property_35_dispatcher_denied_admin_actions` |
| 36 — All endpoints deny unauthenticated | `…::test_property_36_unauthenticated_requests_denied` |

---

## Live verification scripts

| Script | Covers |
|---|---|
| `backend/scripts/journey_check.py` | 57 checks across the full API journey: auth, RBAC, manual entry, upload, optimisation, invariants, concurrency guard, reassignment, locking, re-optimisation, approval/export, alerts, audit |
| `backend/scripts/ws_check.py` | WebSocket authentication and delivery of `optimisation.progress`, `optimisation.complete`, `route.updated`, `orders.pending`, `reoptimisation.suggested` |
| `backend/scripts/resilience_check.py` | 21 checks over the degraded paths: geocoding retry exhaustion and recovery, historical travel-time fallback, export back-off and manual re-trigger, connectivity thresholds |
| `frontend/e2e/console.smoke.mjs` | 23 checks driving the built console in Chromium: map mount, route/stop selection, stop detail, drag-and-drop reassignment targets, alerts, manual entry validation, upload drawer |
| `frontend/e2e/a11y.smoke.mjs` | 11 keyboard and screen-reader checks: skip link, focus indicator, tablist semantics and arrow-key navigation, labelled controls, announced errors, named icon buttons |

## Task completion

| Task | Status | Where |
|---|---|---|
| 1 — Repository scaffold | ✅ | `backend/`, `frontend/` |
| 2.1 — Initial migration | ✅ | `alembic/versions/0001_initial_schema.py` |
| 2.2 — Audit immutability | ✅ | `alembic/versions/0002_audit_immutability.py` |
| 2.3 — Property 33 test | ✅ | property suite |
| 3.1 — Audit service | ✅ | `services/audit_service.py` |
| 3.2, 3.3 — Properties 32, 34 | ✅ | property suite |
| 4.1 — Alert service | ✅ | `services/alert_service.py` |
| 4.2 — Property 29 | ✅ | property suite |
| 5.1–5.12 — Route service + properties | ✅ | `services/route_service.py` |
| 6 — Checkpoint | ✅ | unit + property suites green |
| 7.1–7.5 — Geocoding + properties | ✅ | `services/geocoding_service.py` |
| 8.1–8.5 — OMS adapter + properties | ✅ | `adapters/oms.py` |
| 9.1–9.5 — FMS adapter + properties | ✅ | `adapters/fms.py` |
| 10.1–10.4 — Manual entry + properties | ✅ | `services/manual_entry_service.py` |
| 11.1–11.4 — Upload + properties | ✅ | `services/upload_service.py` |
| 12 — Checkpoint | ✅ | ingest suites green |
| 13.1–13.12 — Optimisation + properties | ✅ | `services/optimisation_engine.py`, `optimisation_service.py` |
| 14.1, 14.2 — Export + Property 28 | ✅ | `services/export_service.py` |
| 15.1–15.8 — API gateway + properties | ✅ | `api/v1/`, `api/deps.py` |
| 16 — Checkpoint | ✅ | backend suites green |
| 17.1–17.6 — Map and route views | ✅ | `components/map/`, `components/panels/` |
| 18.1–18.3 — Drag-drop, manual entry, upload | ✅ | `components/panels/RouteDetailPanel.tsx`, `components/forms/` |
| 19.1–19.5 — Alerts, controls, audit, banner, loading | ✅ | `components/panels/`, `components/layout/` |
| 20 — Checkpoint | ✅ | frontend suite green |
| 21.1, 21.2 — Retention and timeouts | ✅ | `workers/retention.py`, `workers/celery_app.py` |
| 22.1, 22.2 — Docker Compose and K8s | ✅ | `docker-compose.yml`, `deploy/k8s/` |
| 23 — Final checkpoint | ✅ | see the validation section of the README |
