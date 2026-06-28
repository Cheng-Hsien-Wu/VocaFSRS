import { useState, useEffect, useCallback, useRef } from 'react';
import type { PlacementSession, PlacementCard } from '../db/dexie';
import type { PlacementAnswer } from '../domain';
import { syncPlacementEvents, forceSyncPlacementEvents } from '../services/placement-sync';
import { queuePlacementAnswer, queuePlacementUndo } from '../services/placement-event-store';
import {
  bootstrapPlacementSession,
  createPlacementSession,
  placementCheckpointSize,
  persistPlacementPhase,
  placementPhaseForPosition,
  placementSetupError,
  reconcileSessionPosition,
  type PlacementPhase,
  type PlacementSetupError,
} from '../services/placement-session-store';
import { usePlacementCardCache } from './usePlacementCardCache';

type PlacementDispatchAction =
  | { type: 'ANSWER'; result: PlacementAnswer }
  | { type: 'PROBLEMATIC_REASON'; reason: string }
  | { type: 'UNDO' }
  | { type: 'DISMISS_MILESTONE' }
  | { type: 'FLASH_DONE' }
  | { type: 'PAUSE' }
  | { type: 'RESUME' }
  | { type: 'GOTO_CHECKPOINT' };

export type UIPhase = PlacementPhase;

export interface UIState {
  ui: UIPhase;
  flashMeaning: string | null;
  flashCard: PlacementCard | null;
}

export { reconcileSessionPosition } from '../services/placement-session-store';

export function usePlacementSession(requestedCount: number) {
  const [session, setSession] = useState<PlacementSession | null>(null);
  const [currentPosition, setCurrentPosition] = useState<number>(0);
  const [uiState, setUiState] = useState<UIState>({ ui: 'loading', flashMeaning: null, flashCard: null });
  const [errorState, setErrorState] = useState<PlacementSetupError | null>(null);
  const {
    loadedCards,
    loadCachedCards,
    fetchAndCacheChunk,
    resetAndFetchFirstChunk,
  } = usePlacementCardCache(session, currentPosition);
  const answerInFlightRef = useRef(false);
  const answeredPositionGuardRef = useRef<number | null>(null);

  useEffect(() => {
    answeredPositionGuardRef.current = null;
  }, [currentPosition]);

  const retryCreateSession = useCallback(async (availableCount: number) => {
    setErrorState(null);
    setUiState({ ui: 'loading', flashMeaning: null, flashCard: null });
    try {
      const activeSession = await createPlacementSession(availableCount);
      if (activeSession.manifest[0]?.cardId) await resetAndFetchFirstChunk(activeSession);
      setSession(activeSession);
      setCurrentPosition(0);
      setUiState({ ui: 'card', flashMeaning: null, flashCard: null });
      sessionStorage.setItem('placement_resume', '1');
    } catch (err) {
      const setupError = placementSetupError(err);
      if (setupError) {
        setErrorState(setupError);
      } else {
        console.error('Failed to retry session creation:', err);
      }
      setUiState({ ui: 'card', flashMeaning: null, flashCard: null });
    }
  }, [resetAndFetchFirstChunk]);

  const requestedCountRef = useRef(requestedCount);
  const hasInitialized = useRef(false);

  useEffect(() => {
    requestedCountRef.current = requestedCount;
  }, [requestedCount]);

  // Init session
  useEffect(() => {
    if (hasInitialized.current) return;
    hasInitialized.current = true;

    async function init() {
      try {
        const { session: activeSession, position: targetPosition } = await bootstrapPlacementSession(
          requestedCountRef.current,
        );
        const cardMap = await loadCachedCards();

        const chunkNumber = Math.floor(targetPosition / placementCheckpointSize(activeSession));
        const currentCardId = activeSession.manifest[targetPosition]?.cardId;
        if (currentCardId && !cardMap.has(currentCardId)) await fetchAndCacheChunk(activeSession.id, chunkNumber);

        setSession(activeSession);
        setCurrentPosition(targetPosition);

        const reconciledPhase = placementPhaseForPosition(activeSession, targetPosition);

        // Determine UI initial phase based on server status, then fall back to reconciled position.
        if (activeSession.status === 'checkpoint_pending' || activeSession.status === 'audit_active') {
          setUiState({ ui: 'checkpoint', flashMeaning: null, flashCard: null });
        } else if (activeSession.status === 'completed') {
          setUiState({ ui: 'complete', flashMeaning: null, flashCard: null });
        } else {
          const initialPhase =
            activeSession.status === 'active' && reconciledPhase === 'checkpoint'
              ? 'card'
              : reconciledPhase;
          await persistPlacementPhase(activeSession, initialPhase);
          setUiState({ ui: initialPhase, flashMeaning: null, flashCard: null });
        }

        sessionStorage.setItem('placement_resume', '1');
      } catch (err) {
        const setupError = placementSetupError(err);
        if (setupError) {
          setErrorState(setupError);
          setUiState({ ui: 'card', flashMeaning: null, flashCard: null });
        } else {
          console.error('Failed to init placement session:', err);
        }
      }
    }
    init();
  }, [fetchAndCacheChunk, loadCachedCards]);

  const answer = useCallback(
    async (result: PlacementAnswer, reason?: string) => {
      if (!session || (uiState.ui !== 'card' && uiState.ui !== 'problematic_sheet' && uiState.ui !== 'milestone')) return;

      if (result === 'problematic' && !reason) {
        setUiState({ ui: 'problematic_sheet', flashMeaning: null, flashCard: null });
        return;
      }

      if (answerInFlightRef.current || answeredPositionGuardRef.current === currentPosition) return;
      answerInFlightRef.current = true;
      answeredPositionGuardRef.current = currentPosition;
      let storedAnswer = false;
      const currentCardId = session.manifest[currentPosition].cardId;
      const cardData = loadedCards.get(currentCardId);

      try {
        await queuePlacementAnswer(session, currentPosition, result, reason);
        storedAnswer = true;

        // Trigger immediate background sync
        syncPlacementEvents().catch(err => console.error('Immediate sync failed:', err));

        const nextPos = Math.min(currentPosition + 1, session.requestedCount);
        const nextPhase = placementPhaseForPosition(session, nextPos);
        const shouldFlash = result === 'fuzzy' || result === 'unknown';
        setCurrentPosition(nextPos);
        await persistPlacementPhase(session, nextPhase);

        if (shouldFlash) {
          setUiState({
            ui: 'fuzzy_flash',
            flashMeaning: cardData?.chineseMeaning ?? null,
            flashCard: cardData ?? null,
          });
        } else {
          if (nextPhase === 'checkpoint' || nextPhase === 'complete') {
            await forceSyncPlacementEvents().catch(err =>
              console.warn('Force sync at checkpoint failed (offline?):', err)
            );

            setUiState({
              ui: nextPhase,
              flashMeaning: null,
              flashCard: null,
            });

          } else {
            setUiState({
              ui: nextPhase,
              flashMeaning: null,
              flashCard: null,
            });
          }
        }
      } finally {
        if (!storedAnswer) {
          answeredPositionGuardRef.current = null;
        }
        answerInFlightRef.current = false;
      }
    },
    [session, currentPosition, uiState.ui, loadedCards]
  );

  const undo = useCallback(async () => {
    if (!session || currentPosition === 0 || (uiState.ui !== 'card' && uiState.ui !== 'milestone')) return;

    const targetItem = await queuePlacementUndo(session.id);
    if (targetItem) {
      // Trigger sync
      syncPlacementEvents().catch(err => console.error('Immediate sync failed:', err));

      // Recalculate position
      const reconciledNextPos = await reconcileSessionPosition(session.id, session.manifest);
      const nextPos = Math.max(reconciledNextPos, Math.max(currentPosition - 1, 0));
      setCurrentPosition(nextPos);

      const nextPhase = placementPhaseForPosition(session, nextPos);
      setUiState({
        ui: nextPhase,
        flashMeaning: null,
        flashCard: null,
      });
    }
  }, [session, currentPosition, uiState.ui]);

  const dispatch = useCallback(
    (action: PlacementDispatchAction) => {
      switch (action.type) {
        case 'ANSWER':
          answer(action.result);
          break;
        case 'PROBLEMATIC_REASON':
          answer('problematic', action.reason);
          break;
        case 'UNDO':
          undo();
          break;
        case 'DISMISS_MILESTONE':
          setUiState({ ui: 'card', flashMeaning: null, flashCard: null });
          break;
        case 'FLASH_DONE':
          if (uiState.ui === 'fuzzy_flash' && session) {
            (async () => {
              const nextPhase = placementPhaseForPosition(session, currentPosition);
              await persistPlacementPhase(session, nextPhase);

              if (nextPhase === 'checkpoint' || nextPhase === 'complete') {
                await forceSyncPlacementEvents().catch(err =>
                  console.warn('Force sync at checkpoint failed (offline?):', err)
                );

                setUiState({
                  ui: nextPhase,
                  flashMeaning: null,
                  flashCard: null,
                });
              } else {
                setUiState({
                  ui: nextPhase,
                  flashMeaning: null,
                  flashCard: null,
                });
              }
            })();
          } else {
            setUiState({ ui: 'card', flashMeaning: null, flashCard: null });
          }
          break;
        case 'PAUSE':
          setUiState({ ui: 'paused', flashMeaning: null, flashCard: null });
          break;
        case 'RESUME':
          if (session) {
            reconcileSessionPosition(session.id, session.manifest).then(async (nextPos) => {
              setCurrentPosition(nextPos);
              const nextPhase = placementPhaseForPosition(session, nextPos);
              await persistPlacementPhase(session, nextPhase);
              setUiState({
                ui: nextPhase,
                flashMeaning: null,
                flashCard: null,
              });
            });
          } else {
          setUiState({ ui: 'card', flashMeaning: null, flashCard: null });
          }
          break;
        case 'GOTO_CHECKPOINT':
          setUiState({ ui: 'checkpoint', flashMeaning: null, flashCard: null });
          break;
      }
    },
    [answer, undo, uiState.ui, currentPosition, session]
  );

  const currentCardData =
    session && currentPosition < session.requestedCount
      ? loadedCards.get(session.manifest[currentPosition].cardId) || null
      : null;

  return {
    session,
    currentPosition,
    uiState,
    currentCardData,
    dispatch,
    errorState,
    clearError: () => setErrorState(null),
    retryCreateSession,
  };
}
