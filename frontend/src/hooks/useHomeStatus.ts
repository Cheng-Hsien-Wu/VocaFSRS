import { useEffect, useState } from 'react';
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

  useEffect(() => {
    loadResumableState({
      setHasResumable,
      setResumableProgress,
      setHasResumableStudy,
      setResumableStudyProgress,
      setPendingCount,
      setLocalStateLoaded,
    }).catch(err => console.error('Failed to check local state:', err));

    loadStudyPlan({
      setStudyPlan,
      setStudyPlanError,
      setStudyPlanLoaded,
    }).catch(err => console.error('Failed to load study plan:', err));
  }, []);

  return {
    hasResumable,
    resumableProgress,
    hasResumableStudy,
    resumableStudyProgress,
    pendingCount,
    studyPlan,
    studyPlanError,
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
