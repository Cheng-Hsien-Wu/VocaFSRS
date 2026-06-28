import { useCallback, useEffect, useRef, useState } from 'react';

import { db, type PlacementCard, type StudySession } from '../db/dexie';
import { api } from '../services/api';
import {
  bootstrapStudySession,
  createStudySession,
  recordTypedStudyAnswer,
  studySetupError,
  type StudyBootstrap,
  type StudySetupError,
} from '../services/study-session-store';

export type StudyUIPhase =
  | 'loading'
  | 'question'
  | 'summary'
  | 'paused'
  | 'empty'
  | 'complete';

export interface StudyUIState {
  ui: StudyUIPhase;
}

export type StudyLifecycleAction = 'finish' | 'abandon';

export function useStudySession(
  requestedSize: number,
  mode: 'fixed' | 'timed',
  isResume: boolean,
  activationBudget: number | null = null,
) {
  const [session, setSession] = useState<StudySession | null>(null);
  const [currentPosition, setCurrentPosition] = useState(0);
  const [uiState, setUiState] = useState<StudyUIState>({ ui: 'loading' });
  const [errorState, setErrorState] = useState<StudySetupError | null>(null);
  const [lifecycleError, setLifecycleError] = useState<StudyLifecycleAction | null>(null);
  const [loadedCards, setLoadedCards] = useState<Record<string, PlacementCard>>({});

  const sessionRef = useRef<StudySession | null>(null);
  const finishPromiseRef = useRef<Promise<void> | null>(null);
  const hasInitialized = useRef(false);
  const initialOptions = useRef({ requestedSize, isResume });

  useEffect(() => {
    sessionRef.current = session;
  }, [session]);

  const applyBootstrap = useCallback((bootstrap: StudyBootstrap) => {
    const { session: activeSession, position, cards } = bootstrap;
    activeSession.cardsAnswered = Math.max(activeSession.cardsAnswered, position);
    setSession(activeSession);
    setCurrentPosition(position);
    setLoadedCards(cards);

    if (activeSession.manifest.length === 0) {
      setUiState({ ui: 'empty' });
    } else if (position >= activeSession.manifest.length) {
      setUiState({ ui: 'summary' });
    } else {
      setUiState({ ui: 'question' });
    }
  }, []);

  const handleSetupFailure = useCallback((error: unknown, context: string) => {
    console.error(context, error);
    setErrorState(studySetupError(error));
    setUiState({ ui: 'empty' });
  }, []);

  const retryCreateSession = useCallback(async (availableCount: number) => {
    setErrorState(null);
    setUiState({ ui: 'loading' });
    try {
      const bootstrap = await createStudySession(
        availableCount,
        mode,
        activationBudget,
      );
      sessionStorage.setItem('study_resume', '1');
      applyBootstrap(bootstrap);
    } catch (error) {
      handleSetupFailure(error, 'Failed to recreate study session:');
    }
  }, [activationBudget, applyBootstrap, handleSetupFailure, mode]);

  useEffect(() => {
    if (hasInitialized.current) return;
    hasInitialized.current = true;

    bootstrapStudySession(
      initialOptions.current.requestedSize,
      mode,
      initialOptions.current.isResume,
      activationBudget,
    )
      .then(applyBootstrap)
      .catch(error => {
        handleSetupFailure(error, 'Failed to initialize study session:');
      });
  }, [activationBudget, applyBootstrap, handleSetupFailure, mode]);

  const submitTypedAnswer = useCallback(async (typedAnswer: string) => {
    const currentSession = sessionRef.current;
    if (!currentSession || !currentSession.manifest[currentPosition]) return;

    const result = await recordTypedStudyAnswer(
      currentSession,
      currentPosition,
      typedAnswer,
    );
    setSession(result.session);
    if (result.cards) setLoadedCards(result.cards);
    setCurrentPosition(result.position);
    setUiState({
      ui: result.position >= result.session.manifest.length
        ? 'summary'
        : 'question',
    });
  }, [currentPosition]);

  const finishSession = useCallback(async () => {
    if (finishPromiseRef.current) return finishPromiseRef.current;
    const currentSession = sessionRef.current;
    if (!currentSession) return;

    finishPromiseRef.current = (async () => {
      setLifecycleError(null);
      try {
        await api.finishStudySession(currentSession.id);
      } catch (error) {
        setLifecycleError('finish');
        throw error;
      }

      const updatedSession: StudySession = {
        ...currentSession,
        status: 'completed',
        updatedAt: new Date().toISOString(),
      };
      await db.study_sessions.put(updatedSession);
      setSession(updatedSession);
      setUiState({ ui: 'complete' });
      sessionStorage.removeItem('study_resume');
    })().finally(() => {
      finishPromiseRef.current = null;
    });
    return finishPromiseRef.current;
  }, []);

  const abandonSession = useCallback(async () => {
    const currentSession = sessionRef.current;
    if (!currentSession) return;

    setLifecycleError(null);
    try {
      await api.abandonStudySession(currentSession.id);
    } catch (error) {
      setLifecycleError('abandon');
      throw error;
    }

    const updatedSession: StudySession = {
      ...currentSession,
      status: 'abandoned',
      updatedAt: new Date().toISOString(),
    };
    await db.study_sessions.put(updatedSession);
    setSession(updatedSession);
    sessionStorage.removeItem('study_resume');
  }, []);

  return {
    session,
    currentPosition,
    uiState,
    errorState,
    lifecycleError,
    loadedCards,
    retryCreateSession,
    submitTypedAnswer,
    finishSession,
    abandonSession,
    clearLifecycleError: () => setLifecycleError(null),
  };
}
