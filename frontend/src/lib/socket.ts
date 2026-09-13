/** WebSocket client with exponential-backoff reconnection. */

import { API_PREFIX } from '@/lib/api';
import type { WsEvent } from '@/types';

type Listener = (event: WsEvent) => void;

const MAX_BACKOFF_MS = 15_000;

export class RoeSocket {
  private socket: WebSocket | null = null;
  private listeners = new Set<Listener>();
  private attempt = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private closedByUs = false;

  onStateChange: ((connected: boolean) => void) | null = null;

  connect() {
    this.closedByUs = false;
    this.open();
  }

  private url(): string {
    const base = API_PREFIX.startsWith('http')
      ? API_PREFIX
      : `${window.location.origin}${API_PREFIX}`;
    const wsBase = base.replace(/^http/, 'ws');
    return `${wsBase}/ws`;
  }

  private open() {
    if (this.socket && this.socket.readyState <= WebSocket.OPEN) return;
    try {
      this.socket = new WebSocket(this.url());
    } catch {
      this.scheduleReconnect();
      return;
    }

    this.socket.onopen = () => {
      this.attempt = 0;
      this.onStateChange?.(true);
    };

    this.socket.onmessage = (event) => {
      try {
        const parsed = JSON.parse(event.data as string) as WsEvent;
        if (parsed.event === 'ping') return;
        for (const listener of this.listeners) listener(parsed);
      } catch {
        /* ignore malformed frames */
      }
    };

    this.socket.onerror = () => {
      this.socket?.close();
    };

    this.socket.onclose = () => {
      this.onStateChange?.(false);
      if (!this.closedByUs) this.scheduleReconnect();
    };
  }

  private scheduleReconnect() {
    if (this.timer) clearTimeout(this.timer);
    const delay = Math.min(1000 * 2 ** this.attempt, MAX_BACKOFF_MS);
    this.attempt += 1;
    this.timer = setTimeout(() => this.open(), delay);
  }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  disconnect() {
    this.closedByUs = true;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.socket?.close();
    this.socket = null;
    this.onStateChange?.(false);
  }
}

export const roeSocket = new RoeSocket();
