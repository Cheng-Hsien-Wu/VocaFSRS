import { v4 as uuidv4 } from 'uuid';
import { db, type PlacementItem, type PlacementSession } from '../db/dexie';
import type { PlacementAnswer } from '../domain';

export async function queuePlacementAnswer(
  session: PlacementSession,
  position: number,
  result: PlacementAnswer,
  problematicReason?: string,
) {
  const cardId = session.manifest[position].cardId;
  const idempotencyKey = uuidv4();
  const answeredAt = new Date().toISOString();

  await db.transaction('rw', db.placement_items, db.pending_events, async () => {
    await db.placement_items.add({
      id: uuidv4(),
      sessionId: session.id,
      position,
      cardId,
      result,
      problematicReason,
      idempotencyKey,
      answeredAt,
      undone: false,
    });
    await db.pending_events.add({
      sessionId: session.id,
      idempotencyKey,
      type: 'placement',
      payload: {
        idempotency_key: idempotencyKey,
        event_type: 'answer',
        position,
        card_id: cardId,
        result,
        problematic_reason: problematicReason,
        answered_at: answeredAt,
      },
      syncStatus: 'ready',
      createdAt: answeredAt,
    });
  });
}

export async function queuePlacementUndo(sessionId: string): Promise<PlacementItem | null> {
  const items = await db.placement_items.where('sessionId').equals(sessionId).toArray();
  const targetItem = items
    .filter(item => !item.undone)
    .sort((left, right) => right.position - left.position)[0];
  if (!targetItem) return null;

  const idempotencyKey = uuidv4();
  const answeredAt = new Date().toISOString();
  await db.transaction('rw', db.placement_items, db.pending_events, async () => {
    await db.placement_items.update(targetItem.id, { undone: true });
    await db.pending_events.add({
      sessionId,
      idempotencyKey,
      type: 'placement',
      payload: {
        idempotency_key: idempotencyKey,
        event_type: 'undo',
        position: targetItem.position,
        card_id: targetItem.cardId,
        target_event_id: targetItem.idempotencyKey,
        answered_at: answeredAt,
      },
      syncStatus: 'ready',
      createdAt: answeredAt,
    });
  });
  return targetItem;
}
