/**
 * Spreadsheet upload with client-side limit checks and a row-level error table
 * (Requirements 4.1-4.7).
 */

import { Download, FileSpreadsheet, Loader2, Upload, X } from 'lucide-react';
import { useCallback, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { api, ApiError } from '@/lib/api';
import { cn, pluralise } from '@/lib/utils';
import type { RowError, UploadResult } from '@/types';

const MAX_BYTES = 50 * 1024 * 1024;
const MAX_ROWS = 10_000;
const ACCEPTED = ['.csv', '.xlsx', '.xlsm'];

export interface UploadDrawerProps {
  kind: 'orders' | 'vehicles';
  onImported: (result: UploadResult) => void;
}

export function UploadDrawer({ kind, onImported }: UploadDrawerProps) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<UploadResult | null>(null);
  const [errors, setErrors] = useState<RowError[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const [fatal, setFatal] = useState<string | null>(null);

  const reset = () => {
    setResult(null);
    setErrors([]);
    setMessage(null);
    setFatal(null);
  };

  const handleFile = useCallback(
    async (file: File) => {
      reset();

      const extension = file.name.slice(file.name.lastIndexOf('.')).toLowerCase();
      if (!ACCEPTED.includes(extension)) {
        setFatal(`Unsupported file type "${extension}". Upload a .csv or .xlsx file.`);
        return;
      }
      // Requirement 4.7 — reject over-limit files before any processing.
      if (file.size > MAX_BYTES) {
        setFatal(
          `${file.name} is ${(file.size / 1_048_576).toFixed(1)} MB, which exceeds the 50 MB limit. Nothing was imported.`,
        );
        return;
      }
      if (extension === '.csv') {
        const text = await file.text();
        const rows = text.split(/\r?\n/).filter((line) => line.trim().length > 0).length - 1;
        if (rows > MAX_ROWS) {
          setFatal(
            `${file.name} contains ${rows.toLocaleString()} data rows, which exceeds the 10,000 row limit. Nothing was imported.`,
          );
          return;
        }
      }

      setBusy(true);
      try {
        const uploaded =
          kind === 'orders' ? await api.uploadOrders(file) : await api.uploadVehicles(file);
        setResult(uploaded);
        setErrors(uploaded.errors);
        setMessage(uploaded.message);
        onImported(uploaded);
      } catch (error) {
        if (error instanceof ApiError) {
          setFatal(error.message);
          const detailErrors = (error.body.details?.errors ?? []) as RowError[];
          if (Array.isArray(detailErrors)) setErrors(detailErrors);
        } else {
          setFatal('The upload failed. No records were imported — please try again.');
        }
      } finally {
        setBusy(false);
        if (inputRef.current) inputRef.current.value = '';
      }
    },
    [kind, onImported],
  );

  const downloadTemplate = async (format: 'csv' | 'xlsx') => {
    try {
      const blob = await api.downloadTemplate(kind, format);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = `roe-${kind}-template.${format}`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    } catch {
      setFatal('The template could not be downloaded.');
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-muted-foreground">Templates:</span>
        <Button variant="outline" size="sm" onClick={() => downloadTemplate('csv')}>
          <Download className="h-3.5 w-3.5" />
          CSV
        </Button>
        <Button variant="outline" size="sm" onClick={() => downloadTemplate('xlsx')}>
          <Download className="h-3.5 w-3.5" />
          XLSX
        </Button>
      </div>

      <div
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          const file = event.dataTransfer.files?.[0];
          if (file) void handleFile(file);
        }}
        className={cn(
          'rounded-lg border-2 border-dashed p-6 text-center transition-colors',
          dragging ? 'border-primary bg-primary/5' : 'border-border',
        )}
      >
        <FileSpreadsheet className="mx-auto h-7 w-7 text-muted-foreground" aria-hidden="true" />
        <p className="mt-2 text-sm font-medium">
          Drop a {kind} spreadsheet here
        </p>
        <p className="mt-0.5 text-xs text-muted-foreground">
          CSV or XLSX · up to 50 MB and 10,000 rows
        </p>
        <input
          ref={inputRef}
          id={`upload-${kind}`}
          type="file"
          accept={ACCEPTED.join(',')}
          className="sr-only"
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) void handleFile(file);
          }}
        />
        <Button
          variant="outline"
          size="sm"
          className="mt-3"
          disabled={busy}
          onClick={() => inputRef.current?.click()}
        >
          {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Upload className="h-3.5 w-3.5" />}
          {busy ? 'Uploading…' : 'Choose file'}
        </Button>
      </div>

      {fatal && (
        <div
          role="alert"
          className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/10 p-3 text-xs"
        >
          <span className="flex-1">{fatal}</span>
          <button type="button" onClick={reset} aria-label="Dismiss" className="text-muted-foreground">
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      )}

      {result && (
        <div className="rounded-md border border-success/40 bg-success/10 p-3 text-xs">
          <p className="font-medium">
            Imported {pluralise(result.imported, 'row')}, skipped {result.skipped}.
          </p>
          {message && <p className="mt-0.5 text-muted-foreground">{message}</p>}
        </div>
      )}

      {errors.length > 0 && (
        <div className="overflow-hidden rounded-md border border-border">
          <p className="border-b border-border bg-muted px-3 py-1.5 text-xs font-medium">
            {pluralise(errors.length, 'row error')} — these rows were not imported
          </p>
          <div className="max-h-64 overflow-y-auto">
            <table className="w-full text-left text-[11px]">
              <thead className="sticky top-0 bg-card">
                <tr className="border-b border-border">
                  <th scope="col" className="px-3 py-1.5 font-medium">Row</th>
                  <th scope="col" className="px-3 py-1.5 font-medium">Column</th>
                  <th scope="col" className="px-3 py-1.5 font-medium">Reason</th>
                </tr>
              </thead>
              <tbody>
                {errors.map((error, index) => (
                  <tr key={`${error.row_number}-${error.column}-${index}`} className="border-b border-border/60">
                    <td className="px-3 py-1.5 tabular-nums">{error.row_number}</td>
                    <td className="px-3 py-1.5 font-mono">{error.column}</td>
                    <td className="px-3 py-1.5 text-muted-foreground">{error.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
