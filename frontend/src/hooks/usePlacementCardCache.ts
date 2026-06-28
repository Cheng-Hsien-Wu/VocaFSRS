import { useCallback, useEffect, useRef, useState } from 'react';
import { db, type PlacementCard, type PlacementSession } from '../db/dexie';
import { api } from '../services/api';
import { placementCheckpointSize } from '../services/placement-session-store';

export function usePlacementCardCache(session: PlacementSession | null, currentPosition: number) {
  const [loadedCards, setLoadedCards] = useState<Map<string, PlacementCard>>(new Map());
  const fetchedChunks = useRef<Set<number>>(new Set());

  const loadCachedCards = useCallback(async () => {
    const cards = await db.placement_cards.toArray();
    const cardMap = new Map(cards.map(card => [card.id, card]));
    setLoadedCards(cardMap);
    return cardMap;
  }, []);

  const fetchAndCacheChunk = useCallback(async (sessionId: string, chunkNumber: number) => {
    if (fetchedChunks.current.has(chunkNumber)) return [];
    fetchedChunks.current.add(chunkNumber);
    try {
      const cards = await api.getPlacementChunk(sessionId, chunkNumber);
      if (cards.length > 0) {
        await db.placement_cards.bulkPut(cards);
        setLoadedCards(previous => {
          const next = new Map(previous);
          cards.forEach(card => next.set(card.id, card));
          return next;
        });
      }
      return cards;
    } catch (error) {
      fetchedChunks.current.delete(chunkNumber);
      console.error(`Failed to fetch chunk ${chunkNumber}:`, error);
      return [];
    }
  }, []);

  const resetAndFetchFirstChunk = useCallback(async (activeSession: PlacementSession) => {
    fetchedChunks.current.clear();
    const cards = await fetchAndCacheChunk(activeSession.id, 0);
    setLoadedCards(new Map(cards.map(card => [card.id, card])));
  }, [fetchAndCacheChunk]);

  useEffect(() => {
    if (!session) return;
    const checkpointSize = placementCheckpointSize(session);
    const chunkNumber = Math.floor(currentPosition / checkpointSize);
    const currentCardId = session.manifest[currentPosition]?.cardId;
    if (currentCardId && !loadedCards.has(currentCardId)) {
      const timer = window.setTimeout(() => {
        void fetchAndCacheChunk(session.id, chunkNumber);
      }, 0);
      return () => window.clearTimeout(timer);
    }

    const relativePosition = currentPosition % checkpointSize;
    if (relativePosition < checkpointSize * 0.7) return;
    const nextChunkNumber = chunkNumber + 1;
    const nextCardId = session.manifest[nextChunkNumber * checkpointSize]?.cardId;
    if (nextCardId && !loadedCards.has(nextCardId)) {
      const timer = window.setTimeout(() => {
        void fetchAndCacheChunk(session.id, nextChunkNumber);
      }, 0);
      return () => window.clearTimeout(timer);
    }
  }, [currentPosition, fetchAndCacheChunk, loadedCards, session]);

  return {
    loadedCards,
    loadCachedCards,
    fetchAndCacheChunk,
    resetAndFetchFirstChunk,
  };
}
