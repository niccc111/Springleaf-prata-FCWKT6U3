"""Initial ROE schema.

Creates the ``geopoint`` composite type, every table, check constraint, and
index from the design document, plus the operational tables the design refers
to (``error_log``, ``export_jobs``, ``integration_status``,
``travel_time_samples``).

Task 2.1 — Requirements: 5.2, 5.3, 11.6, 16.1
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

import app.db.types

revision = '0001_initial'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # GeoPoint composite type backing every lat/lon column.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'geopoint') THEN
                CREATE TYPE geopoint AS (
                    latitude  DECIMAL(10, 7),
                    longitude DECIMAL(11, 7)
                );
            END IF;
        END
        $$;
        """
    )
    op.create_table('alerts',
    sa.Column('alert_id', sa.UUID(), nullable=False),
    sa.Column('alert_type', sa.Text(), nullable=False),
    sa.Column('severity', sa.Text(), nullable=False),
    sa.Column('entity_type', sa.Text(), nullable=False),
    sa.Column('entity_id', sa.UUID(), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('context', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('raised_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('acknowledged', sa.Boolean(), nullable=False),
    sa.Column('acknowledged_by', sa.UUID(), nullable=True),
    sa.Column('acknowledged_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("alert_type IN ('overload', 'late_delivery', 'impossible_order', 'export_failure')", name=op.f('ck_alerts_alerts_type')),
    sa.CheckConstraint("entity_type IN ('vehicle', 'order', 'route')", name=op.f('ck_alerts_alerts_entity_type')),
    sa.CheckConstraint("severity IN ('warning', 'critical')", name=op.f('ck_alerts_alerts_severity')),
    sa.PrimaryKeyConstraint('alert_id', name=op.f('pk_alerts'))
    )
    op.create_index('idx_alerts_acknowledged', 'alerts', ['acknowledged'], unique=False, postgresql_where=sa.text('acknowledged = FALSE'))
    op.create_index('idx_alerts_entity', 'alerts', ['entity_type', 'entity_id'], unique=False)
    op.create_index('idx_alerts_raised_at', 'alerts', ['raised_at'], unique=False)
    op.create_table('audit_log',
    sa.Column('log_id', sa.UUID(), nullable=False),
    sa.Column('entity_id', sa.UUID(), nullable=False),
    sa.Column('entity_type', sa.Text(), nullable=False),
    sa.Column('old_state', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('new_state', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('action', sa.Text(), nullable=False),
    sa.Column('acting_user', sa.UUID(), nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True, precision=3), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("entity_type IN ('order', 'vehicle', 'route', 'alert', 'user')", name=op.f('ck_audit_log_audit_entity_type')),
    sa.PrimaryKeyConstraint('log_id', name=op.f('pk_audit_log'))
    )
    op.create_index('idx_audit_acting_user', 'audit_log', ['acting_user'], unique=False)
    op.create_index('idx_audit_created_at', 'audit_log', ['created_at'], unique=False)
    op.create_index('idx_audit_entity', 'audit_log', ['entity_type', 'entity_id', 'created_at'], unique=False)
    op.create_table('error_log',
    sa.Column('error_id', sa.UUID(), nullable=False),
    sa.Column('source', sa.Text(), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('error_id', name=op.f('pk_error_log'))
    )
    op.create_index('idx_error_log_created_at', 'error_log', ['created_at'], unique=False)
    op.create_table('integration_status',
    sa.Column('system', sa.Text(), nullable=False),
    sa.Column('healthy', sa.Boolean(), nullable=False),
    sa.Column('message', sa.Text(), nullable=True),
    sa.Column('last_success_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('degraded_since', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('system', name=op.f('pk_integration_status'))
    )
    op.create_table('optimisation_runs',
    sa.Column('run_id', sa.UUID(), nullable=False),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('run_type', sa.Text(), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('locked_excluded_count', sa.Integer(), nullable=True),
    sa.Column('orders_considered', sa.Integer(), nullable=True),
    sa.Column('orders_assigned', sa.Integer(), nullable=True),
    sa.Column('orders_unassigned', sa.Integer(), nullable=True),
    sa.Column('routes_created', sa.Integer(), nullable=True),
    sa.Column('progress_pct', sa.Integer(), nullable=False),
    sa.Column('progress_message', sa.Text(), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('solver_status', sa.Text(), nullable=True),
    sa.Column('duration_seconds', sa.Float(), nullable=True),
    sa.Column('diff', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('initiated_by', sa.UUID(), nullable=False),
    sa.CheckConstraint("run_type IN ('initial', 'reoptimise')", name=op.f('ck_optimisation_runs_runs_type')),
    sa.CheckConstraint("status IN ('in_progress', 'completed', 'timed_out', 'aborted')", name=op.f('ck_optimisation_runs_runs_status')),
    sa.PrimaryKeyConstraint('run_id', name=op.f('pk_optimisation_runs'))
    )
    op.create_index('idx_runs_started_at', 'optimisation_runs', ['started_at'], unique=False)
    op.create_index('idx_runs_status', 'optimisation_runs', ['status'], unique=False)
    op.create_table('orders',
    sa.Column('order_id', sa.UUID(), nullable=False),
    sa.Column('source', sa.Text(), nullable=False),
    sa.Column('pickup_location', app.db.types.GeoPointType(), nullable=True),
    sa.Column('delivery_location', app.db.types.GeoPointType(), nullable=True),
    sa.Column('delivery_address', sa.Text(), nullable=False),
    sa.Column('cargo_weight_kg', sa.Numeric(precision=10, scale=3), nullable=False),
    sa.Column('cargo_volume_m3', sa.Numeric(precision=10, scale=3), nullable=True),
    sa.Column('time_window_start', sa.DateTime(timezone=True), nullable=True),
    sa.Column('time_window_end', sa.DateTime(timezone=True), nullable=True),
    sa.Column('priority', sa.Text(), nullable=False),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('service_duration_min', sa.Integer(), nullable=False),
    sa.Column('geocode_review', sa.Boolean(), nullable=False),
    sa.Column('geocode_confidence', sa.Float(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('external_ref', sa.Text(), nullable=True),
    sa.CheckConstraint("priority IN ('standard', 'priority')", name=op.f('ck_orders_orders_priority')),
    sa.CheckConstraint("source IN ('OMS', 'manual', 'spreadsheet')", name=op.f('ck_orders_orders_source')),
    sa.CheckConstraint("status IN ('unassigned', 'assigned', 'in_transit', 'delivered', 'failed')", name=op.f('ck_orders_orders_status')),
    sa.CheckConstraint('cargo_volume_m3 IS NULL OR cargo_volume_m3 >= 0', name=op.f('ck_orders_orders_cargo_volume_non_negative')),
    sa.CheckConstraint('cargo_weight_kg >= 0', name=op.f('ck_orders_orders_cargo_weight_non_negative')),
    sa.CheckConstraint('service_duration_min >= 0', name=op.f('ck_orders_orders_service_duration_non_negative')),
    sa.CheckConstraint('time_window_start IS NULL OR time_window_end IS NULL OR time_window_start <= time_window_end', name=op.f('ck_orders_time_window_order')),
    sa.PrimaryKeyConstraint('order_id', name=op.f('pk_orders')),
    sa.UniqueConstraint('external_ref', name=op.f('uq_orders_external_ref'))
    )
    op.create_index('idx_orders_created_at', 'orders', ['created_at'], unique=False)
    op.create_index('idx_orders_external_ref', 'orders', ['external_ref'], unique=False, postgresql_where=sa.text('external_ref IS NOT NULL'))
    op.create_index('idx_orders_status', 'orders', ['status'], unique=False)
    op.create_table('travel_time_samples',
    sa.Column('from_key', sa.String(length=32), nullable=False),
    sa.Column('to_key', sa.String(length=32), nullable=False),
    sa.Column('sample_count', sa.Integer(), nullable=False),
    sa.Column('avg_duration_seconds', sa.Float(), nullable=False),
    sa.Column('avg_distance_metres', sa.Float(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('from_key', 'to_key', name=op.f('pk_travel_time_samples'))
    )
    op.create_table('users',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('email', sa.Text(), nullable=False),
    sa.Column('full_name', sa.Text(), nullable=True),
    sa.Column('password_hash', sa.Text(), nullable=True),
    sa.Column('role', sa.Text(), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("role IN ('dispatcher', 'administrator')", name=op.f('ck_users_users_role')),
    sa.PrimaryKeyConstraint('user_id', name=op.f('pk_users')),
    sa.UniqueConstraint('email', name=op.f('uq_users_email'))
    )
    op.create_table('vehicles',
    sa.Column('vehicle_id', sa.UUID(), nullable=False),
    sa.Column('source', sa.Text(), nullable=False),
    sa.Column('registration', sa.Text(), nullable=False),
    sa.Column('capacity_weight_kg', sa.Numeric(precision=10, scale=3), nullable=False),
    sa.Column('capacity_volume_m3', sa.Numeric(precision=10, scale=3), nullable=True),
    sa.Column('depot_location', app.db.types.GeoPointType(), nullable=False),
    sa.Column('operating_hours_start', sa.Time(), nullable=False),
    sa.Column('operating_hours_end', sa.Time(), nullable=False),
    sa.Column('available', sa.Boolean(), nullable=False),
    sa.Column('driver_id', sa.UUID(), nullable=True),
    sa.Column('driver_name', sa.Text(), nullable=True),
    sa.Column('external_ref', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("source IN ('FMS', 'manual', 'spreadsheet')", name=op.f('ck_vehicles_vehicles_source')),
    sa.CheckConstraint('capacity_volume_m3 IS NULL OR capacity_volume_m3 > 0', name=op.f('ck_vehicles_vehicles_capacity_volume_positive')),
    sa.CheckConstraint('capacity_weight_kg > 0', name=op.f('ck_vehicles_vehicles_capacity_weight_positive')),
    sa.PrimaryKeyConstraint('vehicle_id', name=op.f('pk_vehicles')),
    sa.UniqueConstraint('external_ref', name=op.f('uq_vehicles_external_ref'))
    )
    op.create_index('idx_vehicles_available', 'vehicles', ['available'], unique=False)
    op.create_index('idx_vehicles_external_ref', 'vehicles', ['external_ref'], unique=False, postgresql_where=sa.text('external_ref IS NOT NULL'))
    op.create_table('geocoding_queue',
    sa.Column('queue_id', sa.UUID(), nullable=False),
    sa.Column('order_id', sa.UUID(), nullable=False),
    sa.Column('address', sa.Text(), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('next_retry', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('resolved', sa.Boolean(), nullable=False),
    sa.Column('exhausted', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['order_id'], ['orders.order_id'], name=op.f('fk_geocoding_queue_order_id_orders'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('queue_id', name=op.f('pk_geocoding_queue')),
    sa.UniqueConstraint('order_id', name='uq_geocoding_queue_order_id')
    )
    op.create_index('idx_geocoding_queue_next_retry', 'geocoding_queue', ['next_retry'], unique=False)
    op.create_table('routes',
    sa.Column('route_id', sa.UUID(), nullable=False),
    sa.Column('vehicle_id', sa.UUID(), nullable=False),
    sa.Column('optimisation_run_id', sa.UUID(), nullable=False),
    sa.Column('driver_id', sa.UUID(), nullable=True),
    sa.Column('total_distance_km', sa.Numeric(precision=10, scale=3), nullable=False),
    sa.Column('total_duration_min', sa.Integer(), nullable=False),
    sa.Column('total_weight_kg', sa.Numeric(precision=10, scale=3), nullable=False),
    sa.Column('total_volume_m3', sa.Numeric(precision=10, scale=3), nullable=True),
    sa.Column('locked', sa.Boolean(), nullable=False),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('data_quality_warning', sa.Boolean(), nullable=False),
    sa.Column('data_quality_message', sa.Text(), nullable=True),
    sa.Column('needs_reoptimisation', sa.Boolean(), nullable=False),
    sa.Column('priority_relaxed', sa.Boolean(), nullable=False),
    sa.Column('geometry', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('travel_times_updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('draft', 'approved', 'dispatched', 'completed')", name=op.f('ck_routes_routes_status')),
    sa.CheckConstraint("status NOT IN ('dispatched', 'completed') OR locked = TRUE", name=op.f('ck_routes_dispatched_routes_locked')),
    sa.CheckConstraint('total_distance_km >= 0', name=op.f('ck_routes_routes_distance_non_negative')),
    sa.CheckConstraint('total_duration_min >= 0', name=op.f('ck_routes_routes_duration_non_negative')),
    sa.CheckConstraint('total_volume_m3 IS NULL OR total_volume_m3 >= 0', name=op.f('ck_routes_routes_volume_non_negative')),
    sa.CheckConstraint('total_weight_kg >= 0', name=op.f('ck_routes_routes_weight_non_negative')),
    sa.ForeignKeyConstraint(['optimisation_run_id'], ['optimisation_runs.run_id'], name=op.f('fk_routes_optimisation_run_id_optimisation_runs')),
    sa.ForeignKeyConstraint(['vehicle_id'], ['vehicles.vehicle_id'], name=op.f('fk_routes_vehicle_id_vehicles')),
    sa.PrimaryKeyConstraint('route_id', name=op.f('pk_routes'))
    )
    op.create_index('idx_routes_completed_at', 'routes', ['completed_at'], unique=False)
    op.create_index('idx_routes_run_id', 'routes', ['optimisation_run_id'], unique=False)
    op.create_index('idx_routes_status', 'routes', ['status'], unique=False)
    op.create_index('idx_routes_vehicle_id', 'routes', ['vehicle_id'], unique=False)
    op.create_table('export_jobs',
    sa.Column('job_id', sa.UUID(), nullable=False),
    sa.Column('route_id', sa.UUID(), nullable=False),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('attempt_log', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('cancelled', sa.Boolean(), nullable=False),
    sa.Column('initiated_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('pending', 'in_progress', 'succeeded', 'failed', 'cancelled')", name=op.f('ck_export_jobs_export_jobs_status')),
    sa.ForeignKeyConstraint(['route_id'], ['routes.route_id'], name=op.f('fk_export_jobs_route_id_routes'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('job_id', name=op.f('pk_export_jobs'))
    )
    op.create_index('idx_export_jobs_route_id', 'export_jobs', ['route_id'], unique=False)
    op.create_table('stops',
    sa.Column('stop_id', sa.UUID(), nullable=False),
    sa.Column('route_id', sa.UUID(), nullable=False),
    sa.Column('location', app.db.types.GeoPointType(), nullable=False),
    sa.Column('address', sa.Text(), nullable=False),
    sa.Column('sequence_number', sa.Integer(), nullable=False),
    sa.Column('eta', sa.DateTime(timezone=True), nullable=False),
    sa.Column('departure', sa.DateTime(timezone=True), nullable=True),
    sa.Column('time_window_start', sa.DateTime(timezone=True), nullable=True),
    sa.Column('time_window_end', sa.DateTime(timezone=True), nullable=True),
    sa.Column('service_duration_min', sa.Integer(), nullable=False),
    sa.Column('distance_from_previous_km', sa.Numeric(precision=10, scale=3), nullable=False),
    sa.Column('travel_time_from_previous_min', sa.Integer(), nullable=False),
    sa.Column('has_priority_order', sa.Boolean(), nullable=False),
    sa.CheckConstraint('sequence_number >= 1', name=op.f('ck_stops_stops_sequence_positive')),
    sa.CheckConstraint('service_duration_min >= 0', name=op.f('ck_stops_stops_service_duration_non_negative')),
    sa.ForeignKeyConstraint(['route_id'], ['routes.route_id'], name=op.f('fk_stops_route_id_routes'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('stop_id', name=op.f('pk_stops')),
    sa.UniqueConstraint('route_id', 'sequence_number', name='uq_stops_route_sequence')
    )
    op.create_index('idx_stops_route_id', 'stops', ['route_id'], unique=False)
    op.create_table('stop_orders',
    sa.Column('stop_id', sa.UUID(), nullable=False),
    sa.Column('order_id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['order_id'], ['orders.order_id'], name=op.f('fk_stop_orders_order_id_orders')),
    sa.ForeignKeyConstraint(['stop_id'], ['stops.stop_id'], name=op.f('fk_stop_orders_stop_id_stops'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('stop_id', 'order_id', name=op.f('pk_stop_orders'))
    )
    op.create_index('idx_stop_orders_order_id', 'stop_orders', ['order_id'], unique=False)


def downgrade() -> None:
    op.drop_index('idx_stop_orders_order_id', table_name='stop_orders')
    op.drop_table('stop_orders')
    op.drop_index('idx_stops_route_id', table_name='stops')
    op.drop_table('stops')
    op.drop_index('idx_export_jobs_route_id', table_name='export_jobs')
    op.drop_table('export_jobs')
    op.drop_index('idx_routes_vehicle_id', table_name='routes')
    op.drop_index('idx_routes_status', table_name='routes')
    op.drop_index('idx_routes_run_id', table_name='routes')
    op.drop_index('idx_routes_completed_at', table_name='routes')
    op.drop_table('routes')
    op.drop_index('idx_geocoding_queue_next_retry', table_name='geocoding_queue')
    op.drop_table('geocoding_queue')
    op.drop_index('idx_vehicles_external_ref', table_name='vehicles', postgresql_where=sa.text('external_ref IS NOT NULL'))
    op.drop_index('idx_vehicles_available', table_name='vehicles')
    op.drop_table('vehicles')
    op.drop_table('users')
    op.drop_table('travel_time_samples')
    op.drop_index('idx_orders_status', table_name='orders')
    op.drop_index('idx_orders_external_ref', table_name='orders', postgresql_where=sa.text('external_ref IS NOT NULL'))
    op.drop_index('idx_orders_created_at', table_name='orders')
    op.drop_table('orders')
    op.drop_index('idx_runs_status', table_name='optimisation_runs')
    op.drop_index('idx_runs_started_at', table_name='optimisation_runs')
    op.drop_table('optimisation_runs')
    op.drop_table('integration_status')
    op.drop_index('idx_error_log_created_at', table_name='error_log')
    op.drop_table('error_log')
    op.drop_index('idx_audit_entity', table_name='audit_log')
    op.drop_index('idx_audit_created_at', table_name='audit_log')
    op.drop_index('idx_audit_acting_user', table_name='audit_log')
    op.drop_table('audit_log')
    op.drop_index('idx_alerts_raised_at', table_name='alerts')
    op.drop_index('idx_alerts_entity', table_name='alerts')
    op.drop_index('idx_alerts_acknowledged', table_name='alerts', postgresql_where=sa.text('acknowledged = FALSE'))
    op.drop_table('alerts')
    op.execute("DROP TYPE IF EXISTS geopoint")
