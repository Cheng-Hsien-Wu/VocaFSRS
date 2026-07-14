import { useCallback, useEffect, useRef, useState } from 'react';
import { db } from '../db/dexie';
import {
  ACTIVE_PLACEMENT_STATUSES,
  ACTIVE_STUDY_STATUSES,
  type StudyPlanInfo,
} from '../domain';
import { api } from '../services/api';
import {
  cacheServerStudySession,
  reconcileStudyPosition,
} from '../services/study-session-store';
import { reconcileSessionPosition } from './usePlacementSession';
import { parseApiDate } from '../utils/datetime';

interface ServerPlacementManifestItem {
  position: number;
  card_id: string;
}

export function useHomeStatus() {
  const [hasResumable, setHasResumable] = useState(false);
  const [resumableProgress, setResumableProgress] = useState<{ current: number; total: number } | null>(null);
  const [hasResumableStudy, setHasResumableStudy] = useState(false);
  const [resumableStudyProgress, setResumableStudyProgress] = useState<{ current: number; total: number } | null>(null);
  const [pendingCount, setPendingCount] = useState<number | null>(null);
  const [studyPlan, setStudyPlan] = useState<StudyPlanInfo | null>(null);
  const [studyPlanError, setStudyPlanError] = useState<string>('');
  const [localStateLoaded, setLocalStateLoaded] = useState(false);
  const [studyPlanLoaded, setStudyPlanLoaded] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const mountedRef = useRef(false);
  const refreshRequestIdRef = useRef(0);
  const refreshInFlightRef = useRef<Promise<void> | null>(null);

  const refresh = useCallback(() => {
    if (refreshInFlightRef.current) return refreshInFlightRef.current;

    const requestId = refreshRequestIdRef.current + 1;
    refreshRequestIdRef.current = requestId;
    const isCurrent = () => mountedRef.current && refreshRequestIdRef.current === requestId;
    if (mountedRef.current) setIsRefreshing(true);

    const request = Promise.all([
      loadResumableState({
        setHasResumable: value => { if (isCurrent()) setHasResumable(value); },
        setResumableProgress: value => { if (isCurrent()) setResumableProgress(value); },
        setHasResumableStudy: value => { if (isCurrent()) setHasResumableStudy(value); },
        setResumableStudyProgress: value => { if (isCurrent()) setResumableStudyProgress(value); },
        setPendingCount: value => { if (isCurrent()) setPendingCount(value); },
        setLocalStateLoaded: value => { if (isCurrent()) setLocalStateLoaded(value); },
      }),
      loadStudyPlan({
        setStudyPlan: value => { if (isCurrent()) setStudyPlan(value); },
        setStudyPlanError: value => { if (isCurrent()) setStudyPlanError(value); },
        setStudyPlanLoaded: value => { if (isCurrent()) setStudyPlanLoaded(value); },
      }),
    ])
      .then(() => undefined)
      .catch(err => console.error('Failed to refresh home state:', err))
      .finally(() => {
        refreshInFlightRef.current = null;
        if (isCurrent()) setIsRefreshing(false);
      });
    refreshInFlightRef.current = request;
    return request;
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    void refresh();

    const handleFocus = () => { void refresh(); };
    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible') void refresh();
    };
    window.addEventListener('focus', handleFocus);
    document.addEventListener('visibilitychange', handleVisibilityChange);
    return () => {
      mountedRef.current = false;
      window.removeEventListener('focus', handleFocus);
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  }, [refresh]);

  const nextDue = studyPlan?.next_review_due_at ?? studyPlan?.next_due;
  useEffect(() => {
    if (!nextDue) return;
    const delay = parseApiDate(nextDue).getTime() - Date.now();
    if (!Number.isFinite(delay) || delay <= 0) return;
    const timeoutId = window.setTimeout(() => {
      void refresh();
    }, Math.min(delay + 1000, 2_147_483_647));
    return () => window.clearTimeout(timeoutId);
  }, [nextDue, refresh]);

  return {
    hasResumable,
    resumableProgress,
    hasResumableStudy,
    resumableStudyProgress,
    pendingCount,
    studyPlan,
    studyPlanError,
    isRefreshing,
    refresh,
    homeStateLoaded: localStateLoaded && studyPlanLoaded,
  };
}

async function loadResumableState({
  setHasResumable,
  setResumableProgress,
  setHasResumableStudy,
  setResumableStudyProgress,
  setPendingCount,
  setLocalStateLoaded,
}: {
  setHasResumable: (value: boolean) => void;
  setResumableProgress: (value: { current: number; total: number } | null) => void;
  setHasResumableStudy: (value: boolean) => void;
  setResumableStudyProgress: (value: { current: number; total: number } | null) => void;
  setPendingCount: (value: number | null) => void;
  setLocalStateLoaded: (value: boolean) => void;
}) {
  try {
    await loadPlacementResumeState(setHasResumable, setResumableProgress);
    await loadStudyResumeState(setHasResumableStudy, setResumableStudyProgress);
    const count = await db.pending_events.where('syncStatus').anyOf('ready', 'failed').count();
    setPendingCount(count);
  } finally {
    setLocalStateLoaded(true);
  }
}

async function loadPlacementResumeState(
  setHasResumable: (value: boolean) => void,
  setResumableProgress: (value: { current: number; total: number } | null) => void,
) {
  let loadedFromServer = false;
  try {
    const serverSession = await api.getActivePlacementSession();
    loadedFromServer = true;
    if (serverSession) {
      const manifest = (JSON.parse(serverSession.manifest_json) as ServerPlacementManifestItem[]).map(m => ({
        position: m.position,
        cardId: m.card_id,
      }));
      const activeSession = {
        id: serverSession.id,
        requestedCount: serverSession.requested_count,
        status: serverSession.status,
        manifest,
        startedAt: serverSession.started_at,
        updatedAt: new Date().toISOString(),
        checkpointSize: serverSession.checkpoint_size,
      };
      await db.placement_sessions.put(activeSession);
      await db.placement_sessions
        .where('status')
        .anyOf([...ACTIVE_PLACEMENT_STATUSES])
        .and(localSession => localSession.id !== serverSession.id)
        .modify({ status: 'abandoned', updatedAt: new Date().toISOString() });

      const localPosition = await reconcileSessionPosition(activeSession.id, activeSession.manifest);
      const hasLocalPendingEvents = await db.pending_events
        .where('sessionId')
        .equals(serverSession.id)
        .filter(e => e.type === 'placement' && (e.syncStatus === 'draft' || e.syncStatus === 'ready' || e.syncStatus === 'failed'))
        .count();
      const currentPos = hasLocalPendingEvents > 0
        ? localPosition
        : serverSession.current_position ?? localPosition;
      setResumableProgress({ current: currentPos, total: activeSession.requestedCount });
      setHasResumable(true);
    } else {
      await closeLocalPlacementSessions();
      setHasResumable(false);
      setResumableProgress(null);
    }
  } catch (err) {
    console.warn('Failed to load server placement state, falling back to Dexie:', err);
  }

  if (!loadedFromServer) {
    const activeSession = await db.placement_sessions
      .where('status')
      .anyOf([...ACTIVE_PLACEMENT_STATUSES])
      .first();

    if (activeSession) {
      const currentPos = await reconcileSessionPosition(activeSession.id, activeSession.manifest);
      setResumableProgress({ current: currentPos, total: activeSession.requestedCount });
      setHasResumable(true);
    } else {
      setHasResumable(false);
      setResumableProgress(null);
    }
  }
}

async function loadStudyResumeState(
  setHasResumableStudy: (value: boolean) => void,
  setResumableStudyProgress: (value: { current: number; total: number } | null) => void,
) {
  let loadedFromServer = false;
  try {
    const serverStudySession = await api.getActiveStudySession();
    loadedFromServer = true;
    if (serverStudySession) {
      const bootstrap = await cacheServerStudySession(serverStudySession);
      await db.study_sessions
        .where('status')
        .anyOf([...ACTIVE_STUDY_STATUSES])
        .and(session => session.id !== serverStudySession.id)
        .modify({ status: 'abandoned', updatedAt: new Date().toISOString() });
      setResumableStudyProgress({
        current: bootstrap.position,
        total: bootstrap.session.requestedSize,
      });
      setHasResumableStudy(true);
    } else {
      await db.study_sessions
        .where('status')
        .anyOf([...ACTIVE_STUDY_STATUSES])
        .modify({ status: 'abandoned', updatedAt: new Date().toISOString() });
      setHasResumableStudy(false);
      setResumableStudyProgress(null);
    }
  } catch (err) {
    console.warn('Failed to load server study state, falling back to Dexie:', err);
  }

  if (!loadedFromServer) {
    const activeSession = await db.study_sessions
      .where('status')
      .anyOf([...ACTIVE_STUDY_STATUSES])
      .first();

    if (activeSession) {
      const currentPos = await reconcileStudyPosition(activeSession.id, activeSession.manifest);
      setResumableStudyProgress({ current: currentPos, total: activeSession.requestedSize });
      setHasResumableStudy(true);
    } else {
      setHasResumableStudy(false);
      setResumableStudyProgress(null);
    }
  }
}

async function closeLocalPlacementSessions() {
  await db.placement_sessions
    .where('status')
    .anyOf([...ACTIVE_PLACEMENT_STATUSES])
    .modify({ status: 'abandoned', updatedAt: new Date().toISOString() });
}

async function loadStudyPlan({
  setStudyPlan,
  setStudyPlanError,
  setStudyPlanLoaded,
}: {
  setStudyPlan: (value: StudyPlanInfo | null) => void;
  setStudyPlanError: (value: string) => void;
  setStudyPlanLoaded: (value: boolean) => void;
}) {
  try {
    setStudyPlanError('');
    setStudyPlan(await api.getStudyPlan());
  } catch (err) {
    console.error('Failed to load study plan:', err);
    setStudyPlanError(err instanceof Error ? err.message : 'Failed to load study plan');
  } finally {
    setStudyPlanLoaded(true);
  }
}
