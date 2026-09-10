# Requirements Document

## Introduction

The Route Optimisation Engine (ROE) is a logistics planning system that automatically assigns delivery orders to vehicles, plans capacity-aware routes, enforces delivery time windows, and continuously re-optimises routes as conditions change. It exposes an interactive map interface, surfacing route summaries and vehicle load information to dispatchers. Dispatchers retain full manual control — they can reassign orders, lock routes, handle priority deliveries, and receive alerts for overloaded vehicles, late deliveries, and infeasible orders. The engine integrates with existing delivery, fleet, mapping, and order-management systems, and provides spreadsheet upload and manual entry as fallbacks when those integrations are unavailable.

---

## Glossary

- **ROE**: Route Optimisation Engine — the system described by this document.
- **Order**: A delivery task with a pickup or drop-off location, a cargo weight/volume, and an optional delivery time window.
- **Vehicle**: A fleet asset with a defined load capacity (weight and/or volume), home depot location, operating hours, and current availability status.
- **Route**: An ordered sequence of stops assigned to a single vehicle, starting and ending at the vehicle's depot.
- **Stop**: A single waypoint on a route, corresponding to one or more orders at the same location.
- **Time Window**: A [earliest_arrival, latest_arrival] interval within which a stop must be serviced.
- **Capacity Constraint**: A hard upper bound on a vehicle's total cargo weight and/or volume across all stops on its route.
- **Depot**: The start and end location of a vehicle's daily route.
- **Dispatcher**: An operations user who reviews, adjusts, and approves routes before dispatch.
- **Driver**: A field user who executes an assigned route.
- **Priority Order**: An order flagged for expedited delivery that must be scheduled ahead of standard orders during optimisation.
- **Locked Route**: A route that has been manually approved by a Dispatcher and must not be altered by subsequent re-optimisation runs.
- **Optimisation Run**: A single execution of the route-planning algorithm over a set of unassigned orders and available vehicles.
- **OMS**: Order Management System — external source of order data.
- **FMS**: Fleet Management System — external source of vehicle and driver data.
- **Mapping Service**: External provider of geocoding, routing geometry, travel-time matrices, and real-time traffic data.
- **Delivery Platform**: External system that receives completed, dispatched routes.
- **ETA**: Estimated Time of Arrival at a stop.
- **Overload Alert**: A notification raised when a vehicle's assigned cargo exceeds its capacity.
- **Late Delivery Alert**: A notification raised when a stop's ETA falls outside its time window.
- **Impossible Order Alert**: A notification raised when no available vehicle can service an order within its constraints.
- **Spreadsheet Upload**: A dispatcher-initiated bulk import of orders or vehicles via a structured file (CSV or XLSX).
- **Manual Entry Form**: A UI form allowing a dispatcher to create or edit a single order or vehicle record.

---

## Data Schemas

### Order Schema

| Field             | Type                                                              | Required | Description                       |
| ----------------- | ----------------------------------------------------------------- | -------- | --------------------------------- |
| order_id          | string (UUID)                                                     | Yes      | Unique identifier                 |
| source            | enum: OMS \| manual \| spreadsheet                                | Yes      | Origin of the record              |
| pickup_location   | GeoPoint                                                          | No       | Lat/lon of pickup (if applicable) |
| delivery_location | GeoPoint                                                          | Yes      | Lat/lon of delivery address       |
| delivery_address  | string                                                            | Yes      | Human-readable address            |
| cargo_weight_kg   | decimal ≥ 0                                                       | Yes      | Weight of cargo                   |
| cargo_volume_m3   | decimal ≥ 0                                                       | No       | Volume of cargo                   |
| time_window_start | ISO 8601 datetime                                                 | No       | Earliest acceptable arrival       |
| time_window_end   | ISO 8601 datetime                                                 | No       | Latest acceptable arrival         |
| priority          | enum: standard \| priority                                        | Yes      | Delivery priority level           |
| status            | enum: unassigned \| assigned \| in_transit \| delivered \| failed | Yes      | Lifecycle state                   |
| created_at        | ISO 8601 datetime                                                 | Yes      | Record creation timestamp         |
| external_ref      | string                                                            | No       | Reference ID from OMS             |

### Vehicle Schema

| Field                 | Type                               | Required | Description                                 |
| --------------------- | ---------------------------------- | -------- | ------------------------------------------- |
| vehicle_id            | string (UUID)                      | Yes      | Unique identifier                           |
| source                | enum: FMS \| manual \| spreadsheet | Yes      | Origin of the record                        |
| registration          | string                             | Yes      | Vehicle registration number                 |
| capacity_weight_kg    | decimal > 0                        | Yes      | Maximum load weight                         |
| capacity_volume_m3    | decimal > 0                        | No       | Maximum load volume                         |
| depot_location        | GeoPoint                           | Yes      | Start/end location                          |
| operating_hours_start | time (HH:MM)                       | Yes      | Earliest departure time                     |
| operating_hours_end   | time (HH:MM)                       | Yes      | Latest return time                          |
| available             | boolean                            | Yes      | Whether vehicle is available for assignment |
| driver_id             | string (UUID)                      | No       | Assigned driver reference                   |
| external_ref          | string                             | No       | Reference ID from FMS                       |

### Route Schema

| Field               | Type                                               | Required | Description                                  |
| ------------------- | -------------------------------------------------- | -------- | -------------------------------------------- |
| route_id            | string (UUID)                                      | Yes      | Unique identifier                            |
| vehicle_id          | string (UUID)                                      | Yes      | Assigned vehicle                             |
| optimisation_run_id | string (UUID)                                      | Yes      | The run that produced this route             |
| stops               | ordered list of Stop                               | Yes      | Ordered sequence of stops                    |
| total_distance_km   | decimal ≥ 0                                        | Yes      | Sum of inter-stop distances                  |
| total_duration_min  | integer ≥ 0                                        | Yes      | Estimated total travel time                  |
| total_weight_kg     | decimal ≥ 0                                        | Yes      | Sum of cargo weight across all stops         |
| total_volume_m3     | decimal ≥ 0                                        | No       | Sum of cargo volume across all stops         |
| locked              | boolean                                            | Yes      | Whether route is locked from re-optimisation |
| status              | enum: draft \| approved \| dispatched \| completed | Yes      | Lifecycle state                              |
| created_at          | ISO 8601 datetime                                  | Yes      | Record creation timestamp                    |
| updated_at          | ISO 8601 datetime                                  | Yes      | Last modification timestamp                  |

### Stop Schema

| Field                | Type                  | Required | Description                           |
| -------------------- | --------------------- | -------- | ------------------------------------- |
| stop_id              | string (UUID)         | Yes      | Unique identifier                     |
| route_id             | string (UUID)         | Yes      | Parent route                          |
| order_ids            | list of string (UUID) | Yes      | Orders fulfilled at this stop         |
| location             | GeoPoint              | Yes      | Lat/lon of the stop                   |
| address              | string                | Yes      | Human-readable address                |
| sequence_number      | integer ≥ 1           | Yes      | Position in the route                 |
| eta                  | ISO 8601 datetime     | Yes      | Estimated arrival                     |
| time_window_start    | ISO 8601 datetime     | No       | Earliest acceptable arrival           |
| time_window_end      | ISO 8601 datetime     | No       | Latest acceptable arrival             |
| service_duration_min | integer ≥ 0           | Yes      | Time to complete service at this stop |

### Alert Schema

| Field           | Type                                                | Required | Description                           |
| --------------- | --------------------------------------------------- | -------- | ------------------------------------- |
| alert_id        | string (UUID)                                       | Yes      | Unique identifier                     |
| alert_type      | enum: overload \| late_delivery \| impossible_order | Yes      | Category                              |
| severity        | enum: warning \| critical                           | Yes      | Urgency level                         |
| entity_type     | enum: vehicle \| order \| route                     | Yes      | Affected entity type                  |
| entity_id       | string (UUID)                                       | Yes      | ID of the affected entity             |
| message         | string                                              | Yes      | Human-readable description            |
| raised_at       | ISO 8601 datetime                                   | Yes      | When the alert was created            |
| acknowledged    | boolean                                             | Yes      | Whether a Dispatcher has dismissed it |
| acknowledged_by | string (UUID)                                       | No       | Dispatcher user ID                    |
| acknowledged_at | ISO 8601 datetime                                   | No       | Dismissal timestamp                   |

---

## Requirements

### Requirement 1: Order Import from OMS

**User Story:** As a Dispatcher, I want the ROE to automatically import new orders from the OMS, so that I do not have to manually enter each delivery.

#### Acceptance Criteria

1. WHEN the OMS publishes a new order event, THE ROE SHALL ingest the order and create an Order record with `source = OMS` within 30 seconds.
2. WHEN an OMS order event contains a missing or null `delivery_location` or `cargo_weight_kg`, THE ROE SHALL reject the record, create no Order record, and raise an Impossible Order Alert with a descriptive message identifying the missing field by name.
3. WHEN an OMS order event contains a `cargo_weight_kg` value that is not a positive number greater than 0, THE ROE SHALL reject the record, create no Order record, and raise an Impossible Order Alert identifying the invalid value.
4. WHEN an OMS order is successfully ingested, THE ROE SHALL map all OMS fields to the Order Schema and store the OMS order identifier in `external_ref`.
5. IF the OMS integration is unavailable for more than 60 seconds, THEN THE ROE SHALL surface a connectivity warning to all active Dispatchers, and the warning SHALL persist until OMS connectivity is restored.
6. WHEN the ROE receives an OMS order event with an `external_ref` that already exists in the system, THE ROE SHALL discard the duplicate event without creating a new Order record.

---

### Requirement 2: Vehicle Import from FMS

**User Story:** As a Dispatcher, I want the ROE to automatically import vehicle and driver records from the FMS, so that fleet availability is always current without manual entry.

#### Acceptance Criteria

1. WHEN the FMS publishes a vehicle availability update affecting `available`, `capacity_weight_kg`, or `depot_location`, THE ROE SHALL update the corresponding Vehicle record within 30 seconds.
2. WHEN a new vehicle is registered in the FMS, THE ROE SHALL create a Vehicle record with `source = FMS` and store the FMS reference in `external_ref`.
3. WHEN the ROE receives an FMS update for a vehicle whose `external_ref` already exists, THE ROE SHALL update the existing Vehicle record rather than create a duplicate.
4. WHEN the ROE receives an FMS update referencing an unknown vehicle `external_ref`, THE ROE SHALL log the event and notify the Dispatcher with a message identifying the unknown FMS reference.
5. WHEN an FMS vehicle record is missing `capacity_weight_kg` or `depot_location`, THE ROE SHALL reject the record, set `available = false`, and notify the Dispatcher with a message identifying the vehicle by its FMS reference and naming the missing field.
6. IF the FMS integration is unavailable, THEN THE ROE SHALL continue operating using the last known vehicle records and display a stale-data warning to active Dispatchers; WHEN FMS connectivity is restored, THE ROE SHALL automatically dismiss the stale-data warning.

---

### Requirement 3: Manual Order and Vehicle Entry

**User Story:** As a Dispatcher, I want to manually enter orders and vehicles when integrations are unavailable, so that deliveries can still be planned without system dependencies.

#### Acceptance Criteria

1. THE ROE SHALL provide a Manual Entry Form allowing a Dispatcher to create a single Order record with fields: `delivery_address` (string, 1–500 characters), `cargo_weight_kg` (decimal, 0.01–99999.99), `cargo_volume_m3` (decimal ≥ 0, optional), `time_window_start` (ISO 8601 datetime, optional), `time_window_end` (ISO 8601 datetime, optional), and `priority` (enum: standard | priority).
2. THE ROE SHALL provide a Manual Entry Form allowing a Dispatcher to create a single Vehicle record with fields: `registration` (string, 1–20 characters), `capacity_weight_kg` (decimal, 0.01–99999.99), `capacity_volume_m3` (decimal > 0, optional), `depot_location` (GeoPoint), `operating_hours_start` (HH:MM), and `operating_hours_end` (HH:MM).
3. WHEN a Dispatcher submits a Manual Entry Form with one or more required fields missing or containing an out-of-range value, THE ROE SHALL reject the submission, persist no data, preserve the Dispatcher's entered values, and display a field-level validation error identifying each invalid field.
4. WHEN a Dispatcher submits a valid Manual Entry Form, THE ROE SHALL persist the record with `source = manual` within 2 seconds and display a success message identifying the record by its system-assigned identifier.
5. IF the persistence operation fails within the 2-second window, THEN THE ROE SHALL display an error message to the Dispatcher and preserve the entered values so the Dispatcher can retry without re-entering data.
6. THE ROE SHALL allow a Dispatcher to edit any manually entered Order or Vehicle record at any time before the associated Route reaches `status = dispatched`, applying the same validation rules as the creation form.

---

### Requirement 4: Spreadsheet Upload

**User Story:** As a Dispatcher, I want to upload a spreadsheet of orders or vehicles, so that I can bulk-import data quickly when an integration is not available.

#### Acceptance Criteria

1. THE ROE SHALL accept CSV and XLSX spreadsheet uploads for bulk import of Order records, with a maximum file size of 50 MB and a maximum of 10,000 rows per upload.
2. THE ROE SHALL accept CSV and XLSX spreadsheet uploads for bulk import of Vehicle records, with a maximum file size of 50 MB and a maximum of 10,000 rows per upload.
3. THE ROE SHALL publish a downloadable template for each upload type that defines the required column headings, data types, and includes one example data row.
4. WHEN a spreadsheet is uploaded, THE ROE SHALL validate each row against the corresponding schema within 60 seconds and report a row-level error list to the Dispatcher that identifies each row by its row number, the column name that failed, and the reason for failure.
5. WHEN a spreadsheet upload contains at least one valid row, THE ROE SHALL import all valid rows with `source = spreadsheet`, skip all invalid rows, and display the count of imported and skipped rows to the Dispatcher before the session ends.
6. WHEN a spreadsheet upload contains no valid rows, THE ROE SHALL reject the entire upload, persist no records, and present the full validation error list to the Dispatcher.
7. IF an uploaded file exceeds 50 MB or 10,000 rows, THEN THE ROE SHALL reject the upload immediately without processing, persist no records, and display an error message stating the size or row limit that was exceeded.
8. IF a system or processing failure occurs during a spreadsheet upload, THEN THE ROE SHALL persist no records from that upload and display an error message to the Dispatcher indicating that the upload failed.

---

### Requirement 5: Automatic Route Optimisation

**User Story:** As a Dispatcher, I want the ROE to automatically assign orders to vehicles and plan optimal routes, so that I do not need to manually build routes for each vehicle.

#### Acceptance Criteria

1. WHEN a Dispatcher initiates an Optimisation Run, THE ROE SHALL assign all unassigned Orders with `status = unassigned` to available Vehicles and produce a set of Routes within 120 seconds.
2. WHEN producing Routes, THE ROE SHALL ensure that the `total_weight_kg` of each Route does not exceed the `capacity_weight_kg` of the assigned Vehicle.
3. WHERE a Vehicle has a defined `capacity_volume_m3`, WHEN producing Routes, THE ROE SHALL ensure that the `total_volume_m3` of each Route does not exceed the Vehicle's `capacity_volume_m3`.
4. WHEN producing Routes, THE ROE SHALL schedule each Stop so that its ETA falls within the Stop's `time_window_start` and `time_window_end` where a time window is defined.
5. WHEN producing Routes, THE ROE SHALL minimise total fleet travel distance as the primary optimisation objective, subject to capacity and time-window constraints.
6. WHEN producing Routes, THE ROE SHALL minimise total fleet travel time as the secondary optimisation objective where distance is equal.
7. WHEN no feasible assignment exists for an Order given available Vehicles and constraints, THE ROE SHALL leave that Order with `status = unassigned` and raise an Impossible Order Alert for the Dispatcher.
8. WHEN an Optimisation Run completes, THE ROE SHALL present all generated Routes to the Dispatcher for review before any Route is dispatched.
9. IF an Optimisation Run exceeds 120 seconds, THEN THE ROE SHALL abort the run, preserve the `status = unassigned` of all Orders that were being processed, and surface an error message to the Dispatcher indicating the run timed out.
10. WHEN a Dispatcher initiates an Optimisation Run and no Vehicles with `available = true` and `capacity_weight_kg > 0` exist, THE ROE SHALL abort without producing any Routes and surface an error message to the Dispatcher.
11. FOR the purposes of Optimisation Runs, an available Vehicle is defined as a Vehicle record with `available = true` and `capacity_weight_kg > 0`.

---

### Requirement 6: Priority Delivery Handling

**User Story:** As a Dispatcher, I want priority orders to be scheduled ahead of standard orders, so that time-sensitive deliveries are not delayed by lower-priority cargo.

#### Acceptance Criteria

1. WHEN producing Routes, THE ROE SHALL schedule Stops for Orders with `priority = priority` at earlier sequence positions than Stops for Orders with `priority = standard` on the same Vehicle's Route, such that no standard Stop appears at a sequence position earlier than any priority Stop unless a capacity or time-window constraint requires it.
2. WHEN a new Order with `priority = priority` is created after an Optimisation Run, THE ROE SHALL, within 60 seconds, flag all Routes whose assigned Vehicle shares capacity or time-window overlap with the new Order for re-optimisation.
3. WHEN re-optimisation flagging occurs due to a new priority Order, THE ROE SHALL notify the Dispatcher identifying each affected Route by its Route ID within 60 seconds of the Order being created.
4. WHERE a priority Order cannot be scheduled ahead of all standard Orders without violating a capacity or time-window constraint, THE ROE SHALL schedule the priority Order at the earliest feasible sequence position and raise a Late Delivery Alert if the Order's ETA exceeds its `time_window_end` by more than 0 minutes.

---

### Requirement 7: Traffic-Aware Travel Times

**User Story:** As a Dispatcher, I want routes to reflect real-time traffic conditions, so that ETAs are accurate and routes avoid unnecessary delays.

#### Acceptance Criteria

1. WHEN computing travel times between Stops, THE ROE SHALL query the Mapping Service for real-time traffic-adjusted durations for each Stop-to-Stop segment on the Route.
2. WHEN the Mapping Service returns updated travel-time data for an active Route, THE ROE SHALL recalculate all downstream ETAs on that Route within 60 seconds and update the Route record with the revised ETAs and a last-updated timestamp.
3. WHEN a recalculated ETA causes a Stop to fall outside its time window, THE ROE SHALL raise a Late Delivery Alert identifying the Stop, the revised ETA, and the violated time window.
4. IF the Mapping Service is unavailable for more than 30 seconds, THEN THE ROE SHALL fall back to the most recent historical average travel times, display a data-quality warning on all affected Routes, and retry the Mapping Service at intervals of no more than 60 seconds until connectivity is restored.
5. WHEN the Mapping Service returns a response that cannot be parsed or contains invalid data for a segment, THE ROE SHALL retain the prior travel time for that segment and display a data-quality warning on the affected Route.

---

### Requirement 8: Interactive Map Interface

**User Story:** As a Dispatcher, I want to see all routes and stops on an interactive map, so that I can quickly understand the day's delivery plan and identify issues.

#### Acceptance Criteria

1. THE ROE SHALL render an interactive map displaying all Routes for the current planning period, with each Route drawn as a distinct polyline using a unique colour per Vehicle.
2. THE ROE SHALL display a marker for each Stop on the map; WHEN a Dispatcher hovers over or taps a Stop marker, THE ROE SHALL show the stop's sequence number, address, and ETA.
3. THE ROE SHALL display a distinct depot marker for each Vehicle's Depot on the map.
4. WHEN a Dispatcher selects a Route on the map, THE ROE SHALL highlight that Route's polyline and display the Route summary panel showing the assigned Vehicle name, `total_distance_km`, `total_duration_min`, `total_weight_kg`, and vehicle load utilisation percentage.
5. WHEN a Dispatcher selects a Stop marker on the map, THE ROE SHALL display the Stop detail panel showing all associated Order IDs, total cargo weight at that stop, time window (if defined), and current ETA.
6. THE ROE SHALL update the map display within 5 seconds of any Route or Stop change.
7. WHEN the map data fails to load, THE ROE SHALL display an error message to the Dispatcher and provide a manual refresh action.

---

### Requirement 9: Route Summary and Vehicle Load Information

**User Story:** As a Dispatcher, I want to view a summary of each route alongside vehicle load data, so that I can verify the plan before approving it.

#### Acceptance Criteria

1. THE ROE SHALL display a Route summary list panel alongside the map, listing all Routes with their assigned Vehicle name, total stop count, `total_distance_km` (to 2 decimal places), `total_duration_min` (as a whole number), and weight load utilisation percentage (to 1 decimal place).
2. WHEN a Vehicle's `total_weight_kg` exceeds 90% of `capacity_weight_kg`, THE ROE SHALL display a visual warning indicator on that Vehicle's Route summary entry that is visually distinct from the critical indicator.
3. WHEN a Vehicle's `total_weight_kg` equals or exceeds `capacity_weight_kg`, THE ROE SHALL replace the warning indicator with a visual critical indicator and raise an Overload Alert.
4. WHERE a Vehicle has a defined `capacity_volume_m3`, THE ROE SHALL display volume utilisation percentage (to 1 decimal place) alongside weight utilisation in the Route summary.
5. WHEN weight or volume data is missing or invalid for a Route, THE ROE SHALL display "N/A" for the affected utilisation fields and show a data-quality indicator on the Route summary entry.

---

### Requirement 10: Manual Reassignment of Orders

**User Story:** As a Dispatcher, I want to manually reassign an order from one vehicle to another, so that I can correct suboptimal assignments or accommodate special instructions.

#### Acceptance Criteria

1. THE ROE SHALL allow a Dispatcher to drag an Order from one Route to another Route on the map or in the Route summary panel.
2. WHEN an Order is manually reassigned, THE ROE SHALL recalculate the affected Routes' stop sequences, ETAs, total distances, and load totals within 3 seconds of the Dispatcher's action.
3. WHEN a manual reassignment would cause the receiving Vehicle's `total_weight_kg` to exceed `capacity_weight_kg`, THE ROE SHALL reject the reassignment, leave both Routes unchanged, and display a capacity violation error to the Dispatcher.
4. WHERE a Vehicle has a defined `capacity_volume_m3`, WHEN a manual reassignment would cause the receiving Vehicle's `total_volume_m3` to exceed `capacity_volume_m3`, THE ROE SHALL reject the reassignment, leave both Routes unchanged, and display a volume violation error to the Dispatcher.
5. WHEN a manual reassignment causes any Stop's ETA on the receiving Route to fall outside its time window, THE ROE SHALL raise a Late Delivery Alert identifying the affected Stops and prompt the Dispatcher to confirm or cancel the reassignment; no changes SHALL be applied until the Dispatcher confirms.
6. WHEN a Dispatcher manually reassigns an Order to a Route that has `locked = true`, THE ROE SHALL reject the operation, leave both Routes unchanged, and display a message indicating that the destination Route is locked.
7. IF the recalculation following a manual reassignment fails or exceeds 10 seconds, THEN THE ROE SHALL revert both Routes to their prior state and display an error message to the Dispatcher.
8. WHEN a manual reassignment is successfully completed, THE ROE SHALL record an audit entry capturing the Dispatcher's user ID, the source Route ID, the destination Route ID, and a UTC timestamp.

---

### Requirement 11: Route Locking

**User Story:** As a Dispatcher, I want to lock an approved route, so that subsequent re-optimisation runs do not alter a route that has already been communicated to a driver.

#### Acceptance Criteria

1. THE ROE SHALL allow a Dispatcher to lock any Route with `status = draft` or `status = approved` by setting `locked = true`.
2. WHEN a Dispatcher attempts to lock a Route whose `status` is not `draft` or `approved`, THE ROE SHALL reject the operation and display an error message stating that the Route cannot be locked in its current status.
3. WHEN a Route has `locked = true`, THE ROE SHALL exclude it from all subsequent Optimisation Runs; the Optimisation Run output SHALL include a count of Routes excluded due to locking.
4. THE ROE SHALL allow a Dispatcher to unlock a Route by setting `locked = false`, provided the Route has `status` other than `dispatched` or `completed`.
5. WHEN a Dispatcher attempts to unlock a Route with `status = dispatched` or `status = completed`, THE ROE SHALL reject the operation and display an error message stating that the Route cannot be unlocked in its current status.
6. WHEN a Route transitions to `status = dispatched`, THE ROE SHALL automatically set `locked = true`.
7. IF a Dispatcher attempts to set `locked = false` on a Route with `status = dispatched` or `status = completed`, THEN THE ROE SHALL reject the request and display an error message.

---

### Requirement 12: Re-optimisation After Changes

**User Story:** As a Dispatcher, I want to trigger re-optimisation after manual changes or new orders arrive, so that the overall plan stays efficient after adjustments.

#### Acceptance Criteria

1. THE ROE SHALL provide a re-optimise action that a Dispatcher can invoke at any time to run a new Optimisation Run over all Orders with `status = unassigned` and all Routes with `locked = false`.
2. WHEN re-optimisation is triggered, THE ROE SHALL preserve the stop sequences and assignments of all Routes with `locked = true`.
3. WHEN re-optimisation is triggered, THE ROE SHALL unassign Orders from Routes with `locked = false` and pool them with all Orders with `status = unassigned` before reassigning.
4. WHEN a re-optimisation run completes within 120 seconds, THE ROE SHALL present the updated draft Routes to the Dispatcher for review and indicate which Routes changed by showing differences in assigned Orders, stop sequence, and assigned Driver.
5. IF a re-optimisation run exceeds 120 seconds, THEN THE ROE SHALL abort the run, preserve all Routes and Order statuses as they were before the run was triggered, and surface a timeout error to the Dispatcher.
6. WHEN new Orders are ingested from the OMS while at least one Route already exists, THE ROE SHALL notify the Dispatcher that unassigned Orders are pending and offer a one-click re-optimise action.
7. WHEN a Dispatcher triggers re-optimisation while a re-optimisation run is already in progress, THE ROE SHALL reject the new trigger and display a message indicating that a run is already in progress.

---

### Requirement 13: Route Export to Delivery Platform

**User Story:** As a Dispatcher, I want approved routes to be sent to the delivery platform automatically, so that drivers receive their assignments without manual handoff.

#### Acceptance Criteria

1. WHEN a Dispatcher approves a Route by setting `status = approved`, THE ROE SHALL export the Route, including all Stop details and ETAs, to the Delivery Platform within 30 seconds.
2. WHEN the Delivery Platform acknowledges receipt of a Route, THE ROE SHALL update the Route `status` to `dispatched` within 5 seconds of receiving the acknowledgement.
3. IF the Delivery Platform export fails, THEN THE ROE SHALL retry the export up to 3 times using exponential back-off starting at a 5-second interval, doubling each attempt.
4. IF all 3 retry attempts fail, THEN THE ROE SHALL leave the Route `status` as `approved`, surface an export failure alert to the Dispatcher, and allow the Dispatcher to manually re-trigger the export.
5. THE ROE SHALL allow a Dispatcher to manually re-trigger a Route export at any time for Routes with `status = approved`; if a retry sequence is already in progress for that Route, THE ROE SHALL cancel the in-progress sequence before starting the manual export.

---

### Requirement 14: Alerts and Notifications

**User Story:** As a Dispatcher, I want to receive alerts for overloaded vehicles, late deliveries, and impossible orders, so that I can take corrective action before routes are dispatched.

#### Acceptance Criteria

1. WHEN a Route's `total_weight_kg` equals or exceeds the assigned Vehicle's `capacity_weight_kg`, THE ROE SHALL raise an Overload Alert with `severity = critical` referencing the Vehicle ID and Route ID.
2. WHEN a Stop's ETA falls outside its time window by more than 0 minutes, THE ROE SHALL raise a Late Delivery Alert with `severity = warning` referencing the Order ID and Stop ID.
3. WHEN no available Vehicle can be assigned to an Order within its capacity and time-window constraints, THE ROE SHALL raise an Impossible Order Alert with `severity = critical` referencing the Order ID.
4. THE ROE SHALL display all unacknowledged Alerts in a persistent notification panel visible to all Dispatchers on the current planning session; each Alert entry SHALL display the alert type, severity, referenced entity IDs, and creation timestamp.
5. WHEN a Dispatcher acknowledges an Alert, THE ROE SHALL record the `acknowledged_by` user ID and `acknowledged_at` timestamp within 2 seconds, remove the alert from the unacknowledged list for all Dispatchers in the session.
6. WHEN multiple Dispatchers attempt to acknowledge the same Alert simultaneously, THE ROE SHALL record only the first acknowledgement and discard subsequent attempts.
7. THE ROE SHALL retain all Alerts, including acknowledged ones, in the Alert history for a minimum of 90 days from the Alert's creation timestamp; Alerts MAY be purged after this period.
8. IF the ROE fails to raise an Alert due to a system error, THEN THE ROE SHALL log the failure with full details and display an error indication to the Dispatcher within 5 seconds.

---

### Requirement 15: Geocoding and Address Validation

**User Story:** As a Dispatcher, I want delivery addresses to be automatically geocoded, so that orders can be placed accurately on the map and route distances can be calculated.

#### Acceptance Criteria

1. WHEN an Order is created or updated with a `delivery_address`, THE ROE SHALL query the Mapping Service to resolve the address to a `GeoPoint` within 5 seconds and store the result in `delivery_location`.
2. WHEN the Mapping Service returns a single GeoPoint with a confidence score of 0.8 or higher on a 0–1 scale, THE ROE SHALL store that GeoPoint in `delivery_location` without flagging the Order for review.
3. WHEN the Mapping Service returns multiple candidate GeoPoints for an address or a single result with confidence below 0.8, THE ROE SHALL select the highest-confidence result, store it in `delivery_location`, and flag the Order for Dispatcher review.
4. WHEN the Mapping Service cannot resolve an address, THE ROE SHALL set `delivery_location` to null, raise an Impossible Order Alert, and prompt the Dispatcher to supply coordinates manually.
5. WHEN a Dispatcher manually supplies coordinates for an Order, THE ROE SHALL validate that the latitude is in the range −90 to +90 and the longitude is in the range −180 to +180, rejecting values outside these ranges with a descriptive error.
6. IF the Mapping Service is unavailable during geocoding, THEN THE ROE SHALL queue the geocoding request and retry at intervals of no more than 60 seconds up to a maximum of 5 retry attempts, keeping the Order in `status = unassigned` until resolved.
7. IF all 5 retry attempts are exhausted without a successful geocoding response, THEN THE ROE SHALL raise an Impossible Order Alert and prompt the Dispatcher to supply coordinates manually.

---

### Requirement 16: Audit and Data Retention

**User Story:** As an operations manager, I want all route planning actions to be logged, so that I can review past decisions for compliance and performance analysis.

#### Acceptance Criteria

1. THE ROE SHALL record an audit log entry for every state change on Orders, Vehicles, Routes, and Alerts, capturing the entity ID, entity type, old state, new state, acting user ID, and UTC timestamp with millisecond precision.
2. THE ROE SHALL retain audit log entries for a minimum of 365 calendar days from the entry's creation timestamp.
3. THE ROE SHALL retain completed Route records, including all Stops and associated Order data, for a minimum of 365 calendar days from the Route's completion timestamp.
4. WHEN an audit log entry is written, THE ROE SHALL ensure the entry is immutable and cannot be modified or deleted by any user, including Administrators, through any system interface.
5. IF an audit log write fails due to a system error, THEN THE ROE SHALL prevent the associated entity state change from being applied, log the failure details to a separate error log, and display an error message to the acting user.
6. WHEN a Dispatcher or Administrator queries the audit log, THE ROE SHALL return results within 5 seconds for queries spanning the full 365-day retention window.

---

### Requirement 17: Access Control

**User Story:** As a system administrator, I want role-based access control, so that only authorised users can approve routes or modify system data.

#### Acceptance Criteria

1. THE ROE SHALL enforce exactly two user roles: Dispatcher and Administrator.
2. WHILE a user has the Dispatcher role, THE ROE SHALL permit route viewing, manual entry, reassignment, locking, and re-optimisation actions, and SHALL deny all other privileged actions.
3. WHILE a user has the Administrator role, THE ROE SHALL permit all Dispatcher actions plus user management, integration configuration, and audit log access.
4. WHEN an unauthenticated request is made to any ROE API endpoint, THE ROE SHALL return an error response indicating missing or invalid credentials and deny access to the requested resource.
5. WHEN an authenticated user attempts an action outside their role, THE ROE SHALL deny the action and record an audit log entry capturing the user identity, the attempted action, and the UTC timestamp.
6. WHEN a user's role is changed, THE ROE SHALL enforce the new permissions within 60 seconds of the change being saved.

---

### Requirement 18: System Performance

**User Story:** As a Dispatcher, I want the system to respond quickly under normal operational load, so that planning workflows are not impeded by performance issues.

#### Acceptance Criteria

1. THE ROE SHALL serve map rendering and route summary requests within 2 seconds at the 95th percentile under a load of 50 concurrent Dispatcher sessions.
2. THE ROE SHALL complete an Optimisation Run for up to 500 Orders and 50 Vehicles within 120 seconds.
3. WHEN a Dispatcher submits a manual reassignment action, THE ROE SHALL complete all route recalculations and display updated results within 3 seconds.
4. WHEN system latency for any user-facing operation exceeds 5 seconds, THE ROE SHALL display a loading indicator to the user.
5. IF an Optimisation Run or recalculation exceeds its defined time limit, THEN THE ROE SHALL abort the operation, preserve the last known stable state for all affected entities, and display a timeout error message to the Dispatcher.
6. WHILE an Optimisation Run is in progress, THE ROE SHALL display a progress indicator that updates at intervals of no more than 5 seconds.
