/** Audit log browser, administrators only (Requirements 16.6, 17.3). */

import { Search } from 'lucide-react';
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select } from '@/components/ui/select';
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/states';
import { api } from '@/lib/api';
import { formatDateTime, truncateId } from '@/lib/utils';

export interface AuditLogModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

interface Filters {
  entity_type: string;
  entity_id: string;
  acting_user: string;
  action: string;
  from_date: string;
  to_date: string;
}

const EMPTY_FILTERS: Filters = {
  entity_type: '',
  entity_id: '',
  acting_user: '',
  action: '',
  from_date: '',
  to_date: '',
};

export function AuditLogModal({ open, onOpenChange }: AuditLogModalProps) {
  const [draft, setDraft] = useState<Filters>(EMPTY_FILTERS);
  const [applied, setApplied] = useState<Filters>(EMPTY_FILTERS);

  const query = useQuery({
    queryKey: ['audit', applied],
    enabled: open,
    queryFn: () =>
      api.listAudit({
        entity_type: applied.entity_type || undefined,
        entity_id: applied.entity_id || undefined,
        acting_user: applied.acting_user || undefined,
        action: applied.action || undefined,
        from_date: applied.from_date ? new Date(applied.from_date).toISOString() : undefined,
        to_date: applied.to_date ? new Date(applied.to_date).toISOString() : undefined,
        limit: '100',
      }),
  });

  const set = (field: keyof Filters) => (event: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setDraft((prev) => ({ ...prev, [field]: event.target.value }));

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent wide className="max-h-[85vh]">
        <DialogHeader>
          <DialogTitle>Audit log</DialogTitle>
          <DialogDescription>
            Every state change on orders, vehicles, routes, and alerts, retained for 365 days.
            Entries are append-only and cannot be altered.
          </DialogDescription>
        </DialogHeader>

        <form
          className="grid grid-cols-2 gap-3 md:grid-cols-3"
          onSubmit={(event) => {
            event.preventDefault();
            setApplied(draft);
          }}
        >
          <div className="space-y-1.5">
            <Label htmlFor="audit-entity-type">Entity type</Label>
            <Select id="audit-entity-type" value={draft.entity_type} onChange={set('entity_type')}>
              <option value="">All</option>
              <option value="order">Order</option>
              <option value="vehicle">Vehicle</option>
              <option value="route">Route</option>
              <option value="alert">Alert</option>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="audit-entity-id">Entity ID</Label>
            <Input
              id="audit-entity-id"
              value={draft.entity_id}
              onChange={set('entity_id')}
              placeholder="UUID"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="audit-action">Action</Label>
            <Input
              id="audit-action"
              value={draft.action}
              onChange={set('action')}
              placeholder="route.locked"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="audit-user">Acting user</Label>
            <Input
              id="audit-user"
              value={draft.acting_user}
              onChange={set('acting_user')}
              placeholder="UUID"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="audit-from">From</Label>
            <Input id="audit-from" type="date" value={draft.from_date} onChange={set('from_date')} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="audit-to">To</Label>
            <Input id="audit-to" type="date" value={draft.to_date} onChange={set('to_date')} />
          </div>
          <div className="col-span-2 flex gap-2 md:col-span-3">
            <Button type="submit" size="sm">
              <Search className="h-3.5 w-3.5" />
              Search
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => {
                setDraft(EMPTY_FILTERS);
                setApplied(EMPTY_FILTERS);
              }}
            >
              Clear
            </Button>
          </div>
        </form>

        <div className="min-h-0 flex-1 overflow-hidden rounded-md border border-border">
          {query.isLoading && <LoadingState label="Querying the audit log…" />}
          {query.isError && (
            <ErrorState
              title="Audit log unavailable"
              description={(query.error as Error).message}
              onRetry={() => void query.refetch()}
            />
          )}
          {query.data && query.data.items.length === 0 && (
            <EmptyState icon={Search} title="No entries match those filters" />
          )}
          {query.data && query.data.items.length > 0 && (
            <div className="max-h-[45vh] overflow-auto">
              <table className="w-full text-left text-xs">
                <thead className="sticky top-0 bg-muted">
                  <tr>
                    <th scope="col" className="px-3 py-2 font-medium">When (UTC)</th>
                    <th scope="col" className="px-3 py-2 font-medium">Action</th>
                    <th scope="col" className="px-3 py-2 font-medium">Entity</th>
                    <th scope="col" className="px-3 py-2 font-medium">Operator</th>
                    <th scope="col" className="px-3 py-2 font-medium">Change</th>
                  </tr>
                </thead>
                <tbody>
                  {query.data.items.map((entry) => (
                    <tr key={entry.log_id} className="border-t border-border align-top">
                      <td className="whitespace-nowrap px-3 py-2 tabular-nums">
                        {formatDateTime(entry.created_at)}
                      </td>
                      <td className="px-3 py-2 font-mono">{entry.action}</td>
                      <td className="px-3 py-2">
                        <span className="capitalize">{entry.entity_type}</span>{' '}
                        <span className="font-mono text-muted-foreground" title={entry.entity_id}>
                          {truncateId(entry.entity_id)}
                        </span>
                      </td>
                      <td className="px-3 py-2 font-mono text-muted-foreground" title={entry.acting_user}>
                        {truncateId(entry.acting_user)}
                      </td>
                      <td className="px-3 py-2">
                        <details>
                          <summary className="cursor-pointer text-muted-foreground">
                            {entry.old_state ? 'before / after' : 'created'}
                          </summary>
                          <pre className="mt-1 max-h-48 max-w-md overflow-auto rounded bg-muted p-2 text-[10px] leading-relaxed">
{JSON.stringify({ old: entry.old_state, new: entry.new_state }, null, 2)}
                          </pre>
                        </details>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {query.data && (
          <p className="text-xs text-muted-foreground">
            Showing {query.data.items.length} of {query.data.total.toLocaleString()} entries
          </p>
        )}
      </DialogContent>
    </Dialog>
  );
}
