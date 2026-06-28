import type { StudyPlanInfo } from '../domain';
import { formatTaipeiDateTime } from '../utils/datetime';

export type MainAction =
  | { state: 'resume-study'; title: string; detail: string; button: string; onClick: () => void }
  | { state: 'resume-placement'; title: string; detail: string; button: string; onClick: () => void }
  | { state: 'study'; title: string; detail: string; button: string; onClick: () => void }
  | { state: 'placement'; title: string; detail: string; button: string; onClick: () => void }
  | { state: 'done'; title: string; detail: string; button: string; onClick: () => void }
  | { state: 'blocked'; title: string; detail: string; button: string; onClick: () => void }
  | { state: 'error'; title: string; detail: string; button: string; onClick: () => void };

interface MainActionOptions {
  hasResumableStudy: boolean;
  resumableStudyProgress: { current: number; total: number } | null;
  hasResumable: boolean;
  resumableProgress: { current: number; total: number } | null;
  studyPlan: StudyPlanInfo | null;
  studyPlanError: string;
  startStudy: (count: number) => void;
  startPlacement: (count: number) => void;
  startStudyResume: () => void;
  startResume: () => void;
  navigateToImport: () => void;
  navigateToMistakes: () => void;
  navigateToSummary: () => void;
  reloadPage: () => void;
}

export function buildMainAction(options: MainActionOptions): MainAction {
  const {
    hasResumableStudy,
    resumableStudyProgress,
    hasResumable,
    resumableProgress,
    studyPlan,
    studyPlanError,
    startStudy,
    startPlacement,
    startStudyResume,
    startResume,
    navigateToImport,
    navigateToMistakes,
    navigateToSummary,
    reloadPage,
  } = options;

  if (hasResumableStudy) {
    return {
      state: 'resume-study',
      title: '繼續上次複習',
      detail: resumableStudyProgress
        ? `第 ${resumableStudyProgress.current} / ${resumableStudyProgress.total} 題`
        : '你有一輪尚未完成的複習。',
      button: '繼續複習',
      onClick: startStudyResume,
    };
  }
  if (hasResumable) {
    return {
      state: 'resume-placement',
      title: '繼續上次盤點',
      detail: resumableProgress
        ? `第 ${resumableProgress.current} / ${resumableProgress.total} 張`
        : '你有一輪尚未完成的盤點。',
      button: '繼續盤點',
      onClick: startResume,
    };
  }
  if (studyPlanError) {
    return {
      state: 'error',
      title: '連線失敗',
      detail: '無法讀取目前學習狀態。請確認後端服務正在執行，再重新整理。',
      button: '重新整理',
      onClick: reloadPage,
    };
  }
  if (!studyPlan) return placementFallback(startPlacement);

  if (
    studyPlan.availability_state === 'no_cards'
    || studyPlan.placement_status?.status === 'no_cards'
  ) {
    return {
      state: 'blocked',
      title: '先匯入單字',
      detail: '目前沒有可盤點的單字。請先匯入英文與中文釋義，再開始盤點。',
      button: '前往匯入',
      onClick: navigateToImport,
    };
  }
  if (studyPlan.placement_status?.status === 'in_progress') {
    return {
      state: 'resume-placement',
      title: '繼續盤點',
      detail: '你有一輪盤點尚未完成。全部盤點完後才會開放 FSRS 複習。',
      button: '繼續盤點',
      onClick: startResume,
    };
  }
  if (
    studyPlan.availability_state === 'placement_required'
    || studyPlan.placement_status?.status === 'required'
  ) {
    const remaining = studyPlan.placement_status?.remaining_count ?? 0;
    return {
      state: 'placement',
      title: '需要先盤點',
      detail: remaining > 0
        ? `還有 ${remaining} 個單字尚未盤點。全部盤點完後才會開放 FSRS 複習。`
        : '正式複習需要先完成盤點。',
      button: '繼續盤點 100 字',
      onClick: () => startPlacement(100),
    };
  }
  if (studyPlan.availability_state === 'deck_scope_required') {
    return {
      state: 'blocked',
      title: '需要整理單字來源',
      detail: studyPlan.deck_scope_error ?? '目前複習來源不明確，請先回到匯入流程整理單字。',
      button: '前往匯入',
      onClick: navigateToImport,
    };
  }
  if ((studyPlan.pending_adjudication_count ?? 0) > 0) {
    return {
      state: 'blocked',
      title: '先完成上一輪批改',
      detail: '批改完成前不會更新 FSRS 下次複習時間，請回到結果頁確認。',
      button: '查看結果',
      onClick: navigateToSummary,
    };
  }
  if (studyPlan.due_count > 0) {
    return {
      state: 'study',
      title: '開始今日複習',
      detail: `有 ${studyPlan.due_count} 個到期單字。若題數不足，系統會補入待學佇列中的新字。`,
      button: '開始 25 題',
      onClick: () => startStudy(25),
    };
  }

  const pendingNewCount = studyPlan.pending_new_count ?? studyPlan.remaining_new_cards;
  if (pendingNewCount > 0) {
    return {
      state: 'study',
      title: '開始一輪新字複習',
      detail: `待學佇列有 ${pendingNewCount} 個單字；本輪只會取你選的題數，不會一次塞進 5000 題。`,
      button: '開始 25 題',
      onClick: () => startStudy(25),
    };
  }

  const nextDue = studyPlan.next_review_due_at ?? studyPlan.next_due;
  if (nextDue) {
    return {
      state: 'done',
      title: '目前不用複習',
      detail: `下次到期：${formatTaipeiDateTime(nextDue)}`,
      button: '查看錯題與匯出',
      onClick: navigateToMistakes,
    };
  }
  return {
    state: 'placement',
    title: '先開始盤點',
    detail: '盤點是 FSRS 的入口；先篩出不熟與模糊的字，正式複習才有材料。',
    button: '開始盤點 100 字',
    onClick: () => startPlacement(100),
  };
}

export function nextDueLabel(plan: StudyPlanInfo) {
  if ((plan.pending_adjudication_count ?? 0) > 0) return '批改中';
  if (plan.availability_state === 'available_due' || plan.due_count > 0) {
    return '現在';
  }
  if (
    plan.availability_state === 'available_new'
    || (plan.pending_new_count ?? plan.remaining_new_cards) > 0
  ) {
    return '可補新字';
  }
  const nextDue = plan.next_review_due_at ?? plan.next_due;
  if (nextDue) return formatTaipeiDateTime(nextDue);
  if (!plan.started || plan.availability_state === 'not_started') {
    return '尚未開始';
  }
  if (plan.availability_state === 'placement_required') return '盤點未完成';
  if (plan.availability_state === 'no_cards') return '尚無單字';
  return '尚無學習資料';
}

function placementFallback(
  startPlacement: (count: number) => void,
): MainAction {
  return {
    state: 'placement',
    title: '先開始盤點',
    detail: '盤點會把不熟與模糊的字排到前面，已掌握的字只進低優先級驗證。',
    button: '開始盤點 100 字',
    onClick: () => startPlacement(100),
  };
}
