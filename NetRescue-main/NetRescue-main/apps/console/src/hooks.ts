/**
 * hooks.ts — data loading, live events, and shared UI state.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, eventsUrl } from './api'
import type { LabEvent } from './types'

// ---------------------------------------------------------------------------
// Async data
// ---------------------------------------------------------------------------

export interface AsyncState<T> {
  data: T | null
  error: string | null
  loading: boolean
  /** True only for the first load, so refreshes do not blank the screen. */
  initial: boolean
  reload: () => void
}

/**
 * Load data, with cancellation on unmount.
 *
 * `pollMs` turns it into a periodic refresh. Errors are surfaced as the
 * server's own message; a failed refresh never clears data that is already on
 * screen, because showing an empty panel would hide a working result.
 */
export function useAsync<T>(
  fn: () => Promise<T>,
  deps: unknown[] = [],
  pollMs?: number,
): AsyncState<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [initial, setInitial] = useState(true)
  const [tick, setTick] = useState(0)
  const fnRef = useRef(fn)
  fnRef.current = fn

  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined

    const run = async () => {
      try {
        const result = await fnRef.current()
        if (cancelled) return
        setData(result)
        setError(null)
      } catch (err) {
        if (cancelled) return
        setError(err instanceof Error ? err.message : String(err))
      } finally {
        if (!cancelled) {
          setLoading(false)
          setInitial(false)
          if (pollMs) timer = setTimeout(run, pollMs)
        }
      }
    }
    void run()

    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tick, pollMs, ...deps])

  const reload = useCallback(() => {
    setLoading(true)
    setTick((t) => t + 1)
  }, [])

  return { data, error, loading, initial, reload }
}

// ---------------------------------------------------------------------------
// Live events
// ---------------------------------------------------------------------------

export type SocketState = 'connecting' | 'open' | 'closed' | 'error'

export interface EventsResult {
  events: LabEvent[]
  state: SocketState
  connected: boolean
  clear: () => void
}

/**
 * Subscribe to /ws/events with automatic reconnection.
 *
 * Reconnect uses capped exponential backoff so a backend restart is picked up
 * within a second or two but a backend that is down does not turn into a busy
 * loop. The panel shows `state` explicitly, because a silently dead event feed
 * would leave a live-looking screen showing stale data.
 */
export function useEvents(maxHistory = 200): EventsResult {
  const [events, setEvents] = useState<LabEvent[]>([])
  const [state, setState] = useState<SocketState>('connecting')
  const retryRef = useRef(0)
  const socketRef = useRef<WebSocket | null>(null)
  const closedRef = useRef(false)

  const clear = useCallback(() => setEvents([]), [])

  useEffect(() => {
    closedRef.current = false
    let retryTimer: ReturnType<typeof setTimeout> | undefined

    const connect = () => {
      if (closedRef.current) return
      const url = eventsUrl()
      setState((s) => (s === 'open' ? s : 'connecting'))

      let ws: WebSocket
      try {
        ws = new WebSocket(url)
      } catch {
        scheduleReconnect()
        return
      }
      socketRef.current = ws

      ws.onopen = () => {
        retryRef.current = 0
        setState('open')
      }

      ws.onmessage = (event) => {
        let parsed: LabEvent
        try {
          parsed = JSON.parse(event.data as string) as LabEvent
        } catch {
          return
        }
        if (parsed.type === 'ping') return
        setEvents((prev) => {
          const next = [parsed, ...prev]
          return next.length > maxHistory ? next.slice(0, maxHistory) : next
        })
      }

      ws.onerror = () => setState('error')
      ws.onclose = () => {
        socketRef.current = null
        if (!closedRef.current) scheduleReconnect()
        else setState('closed')
      }
    }

    const scheduleReconnect = () => {
      if (closedRef.current) return
      setState('closed')
      const delay = Math.min(1000 * 2 ** retryRef.current, 15000)
      retryRef.current = Math.min(retryRef.current + 1, 4)
      retryTimer = setTimeout(connect, delay)
    }

    connect()
    return () => {
      closedRef.current = true
      if (retryTimer) clearTimeout(retryTimer)
      socketRef.current?.close()
      socketRef.current = null
    }
  }, [maxHistory])

  return { events, state, connected: state === 'open', clear }
}

// ---------------------------------------------------------------------------
// Local UI state
// ---------------------------------------------------------------------------

/** Wrap an async action with pending/error state and a clear-on-next-run. */
export function useAction<A extends unknown[], R>(
  fn: (...args: A) => Promise<R>,
): [
  (...args: A) => Promise<R | undefined>,
  { pending: boolean; error: string | null; result: R | null },
  () => void,
] {
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<R | null>(null)
  const alive = useRef(true)

  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])

  const invoke = useCallback(
    async (...args: A) => {
      setPending(true)
      setError(null)
      try {
        const value = await fn(...args)
        if (alive.current) setResult(value)
        return value
      } catch (err) {
        if (alive.current) {
          setError(
            err instanceof ApiError || err instanceof Error
              ? err.message
              : String(err),
          )
        }
        return undefined
      } finally {
        if (alive.current) setPending(false)
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  )

  const reset = useCallback(() => {
    setError(null)
    setResult(null)
  }, [])

  return [invoke, { pending, error, result }, reset]
}
