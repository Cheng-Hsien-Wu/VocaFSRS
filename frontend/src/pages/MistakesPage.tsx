import { useCallback, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { formatTaipeiDateTime } from '../utils/datetime';
import { MaterialSymbol } from '../components/MaterialSymbol';
import { useConfusionsList, useMistakesList, type MistakesTab } from '../hooks/useMistakesData';
import { useMistakesExport } from '../hooks/useMistakesExport';
import { useModalFocus } from '../hooks/useModalFocus';
import { MistakesExportDialog } from '../components/MistakesExportDialog';

const MISTAKES_TABS: MistakesTab[] = ['mistakes', 'confusions'];
export default function MistakesPage() {
  const navigate = useNavigate();
  const [activeTab, setActiveTab] = useState<MistakesTab>('mistakes');
  const exportTriggerRef = useRef<HTMLButtonElement | null>(null);
  const exportCloseRef = useRef<HTMLButtonElement | null>(null);
  const exportDialogRef = useRef<HTMLDivElement | null>(null);
  const tabButtonRefs = useRef<Record<MistakesTab, HTMLButtonElement | null>>({
    mistakes: null,
    confusions: null,
  });

  const {
    mistakes,
    totalMistakes,
    mistakesPage,
    days,
    setDays,
    ratingFilter,
    setRatingFilter,
    repeatedLapses,
    setRepeatedLapses,
    expandedMistakeWord,
    setExpandedMistakeWord,
    isLoadingMistakes,
    loadMistakes,
  } = useMistakesList(activeTab);

  const {
    confusions,
    totalConfusions,
    confusionsPage,
    orderBy,
    setOrderBy,
    expandedConfusionIdx,
    setExpandedConfusionIdx,
    isLoadingConfusions,
    loadConfusions,
  } = useConfusionsList(activeTab);

  const {
    showExportModal,
    setShowExportModal,
    exportFilterType,
    setExportFilterType,
    exportLimitType,
    setExportLimitType,
    exportCustomLimit,
    setExportCustomLimit,
    exportPreview,
    isExporting,
    copyFeedback,
    handleCopyToClipboard,
    handleDownloadFile,
    openTodayExport,
  } = useMistakesExport();

  const closeExportModal = useCallback(() => {
    setShowExportModal(false);
  }, [setShowExportModal]);

  useModalFocus({
    active: showExportModal,
    containerRef: exportDialogRef,
    initialFocusRef: exportCloseRef,
    returnFocusRef: exportTriggerRef,
    onClose: closeExportModal,
  });

  const focusTab = useCallback((tab: MistakesTab) => {
    setActiveTab(tab);
    window.setTimeout(() => tabButtonRefs.current[tab]?.focus(), 0);
  }, []);

  const handleTabKeyDown = useCallback((event: ReactKeyboardEvent<HTMLButtonElement>, tab: MistakesTab) => {
    const currentIndex = MISTAKES_TABS.indexOf(tab);
    const previousTab = MISTAKES_TABS[(currentIndex + MISTAKES_TABS.length - 1) % MISTAKES_TABS.length];
    const nextTab = MISTAKES_TABS[(currentIndex + 1) % MISTAKES_TABS.length];

    if (event.key === 'ArrowLeft') {
      event.preventDefault();
      focusTab(previousTab);
    } else if (event.key === 'ArrowRight') {
      event.preventDefault();
      focusTab(nextTab);
    } else if (event.key === 'Home') {
      event.preventDefault();
      focusTab(MISTAKES_TABS[0]);
    } else if (event.key === 'End') {
      event.preventDefault();
      focusTab(MISTAKES_TABS[MISTAKES_TABS.length - 1]);
    }
  }, [focusTab]);

  return (
    <div className="full-screen mistakes-page">
      {/* Header */}
      <div className="mistakes-header">
        <div className="mistakes-header-title">
          <button className="nav-back" onClick={() => navigate('/')} aria-label="回首頁">
            <MaterialSymbol name="arrow_back" />
          </button>
          <h1>數據分析與管理</h1>
        </div>
        <button
          id="btn-trigger-export"
          ref={exportTriggerRef}
          onClick={openTodayExport}
          className="btn btn-primary btn-sm"
        >
          匯出 Podcast
        </button>
      </div>

      {/* Tab Switcher */}
      <div
        role="tablist"
        aria-label="錯題分析分頁"
        className="mistakes-tabs"
      >
        {MISTAKES_TABS.map((tab) => (
          <button
            key={tab}
            id={`tab-btn-${tab}`}
            ref={(element) => {
              tabButtonRefs.current[tab] = element;
            }}
            role="tab"
            aria-selected={activeTab === tab}
            aria-controls={`tab-panel-${tab}`}
            tabIndex={activeTab === tab ? 0 : -1}
            type="button"
            onClick={() => setActiveTab(tab)}
            onKeyDown={(event) => handleTabKeyDown(event, tab)}
            className="mistakes-tab"
          >
            {tab === 'mistakes' ? '單字錯題' : '混淆分析'}
          </button>
        ))}
      </div>

      {/* Tab Panels */}
      <div className="page mistakes-content">
        
        {/* Panel 1: Mistakes */}
        {activeTab === 'mistakes' && (
          <div id="tab-panel-mistakes" role="tabpanel" aria-labelledby="tab-btn-mistakes" className="mistakes-tab-panel">
            
            {/* Mistakes Filters */}
            <div className="mistakes-filter-panel">
              {/* Day filter buttons */}
              <div role="group" aria-label="錯題時間範圍" className="mistakes-filter-row">
                <span className="mistakes-filter-label">時間範圍:</span>
                {([7, 30, null] as (number | null)[]).map((d) => (
                  <button
                    key={String(d)}
                    id={`filter-mistake-days-${d === null ? 'all' : d}`}
                    type="button"
                    aria-pressed={days === d}
                    onClick={() => setDays(d)}
                    className="mistakes-filter-option"
                  >
                    {d === 7 ? '7 天' : d === 30 ? '30 天' : '全部'}
                  </button>
                ))}
              </div>

              {/* Rating filter buttons */}
              <div role="group" aria-label="錯題狀態篩選" className="mistakes-filter-row">
                <span className="mistakes-filter-label">狀態篩選:</span>
                {['all', 'Again', 'Hard'].map((r) => (
                  <button
                    key={r}
                    id={`filter-mistake-rating-${r}`}
                    type="button"
                    aria-pressed={ratingFilter === r}
                    onClick={() => setRatingFilter(r)}
                    className="mistakes-filter-option"
                  >
                    {r === 'all' ? '全部' : r === 'Again' ? '再試 (Again)' : '猶豫 (Hard)'}
                  </button>
                ))}
              </div>

              <div className="mistakes-checkbox-row">
                <input
	                  type="checkbox"
	                  id="filter-mistake-lapses"
                    name="filterMistakeLapses"
	                  checked={repeatedLapses}
                  onChange={(e) => setRepeatedLapses(e.target.checked)}
                  className="mistakes-checkbox"
                />
                <label htmlFor="filter-mistake-lapses">
                  重複失誤 (lapses ≥ 2)
                </label>
              </div>
            </div>

            {/* Mistakes List */}
            {mistakes.length === 0 ? (
              <div className="empty-state mistakes-empty-state">
                <MaterialSymbol name="check_circle" fill className="empty-state-icon" />
		                <div className="mistakes-empty-title">沒有符合條件的錯題</div>
		                <div className="mistakes-empty-copy">最新一次仍是 Again 或 Hard 的單字會出現在這裡。</div>
              </div>
            ) : (
              <div className="mistakes-list">
                <div className="mistakes-list-count">
	                  共有 {totalMistakes} 個最新仍未穩定的單字
                </div>
                
                {mistakes.map((m) => (
                  <div key={m.english} className="card content-auto mistakes-entry">
                    <button
                      id={`btn-mistake-word-${m.english}`}
                      onClick={() => setExpandedMistakeWord(expandedMistakeWord === m.english ? null : m.english)}
                      className="mistakes-entry-trigger mistakes-entry-trigger-top"
                    >
                      <div className="mistakes-entry-main">
                        <div className="long-text mistakes-entry-term">
                          {m.english}
                        </div>
                        <div className="long-text mistakes-entry-meaning">
                          {m.part_of_speech ? `[${m.part_of_speech}] ` : ''}{m.chinese_meaning}
                        </div>
                      </div>
                      <div className="mistakes-entry-stats">
                        {m.again_count > 0 && <span className="stat-pill stat-pill-again">再試 {m.again_count}</span>}
                        {m.hard_count > 0 && <span className="stat-pill stat-pill-hard">猶豫 {m.hard_count}</span>}
                        {m.lapses >= 2 && <span className="stat-pill stat-pill-lapse">失誤 {m.lapses}</span>}
                      </div>
                    </button>

                    {expandedMistakeWord === m.english && (
                      <div className="mistakes-entry-details">
                        <div className="mistakes-entry-detail-list">
                          
                          {/* Sense hint */}
                          {m.sense_hint && (
                            <div>
                              <div className="mistakes-detail-label">字義提示</div>
                              <div className="mistakes-detail-value">{m.sense_hint}</div>
                            </div>
                          )}

                          {/* Confusion details */}
                          {m.confused_word && (
                            <div>
                              <div className="mistakes-detail-label mistakes-detail-label-spaced">最近一次錯誤混淆</div>
                              <div className="long-text mistakes-confusion-alert">
                                混淆字: {m.confused_word}
                                {m.selected_wrong_meaning && ` (選了：${m.selected_wrong_meaning})`}
                              </div>
                            </div>
                          )}

                          {/* Example Sentences */}
                          {m.example_sentence && (
                            <div>
                              <div className="mistakes-detail-label">例句</div>
                              <div className="long-text mistakes-example">
                                {m.example_sentence}
                              </div>
                              {m.example_translation && (
                                <div className="long-text mistakes-example-translation">
                                  {m.example_translation}
                                </div>
                              )}
                            </div>
                          )}

                          {m.last_review_time && (
                            <div className="mistakes-timestamp">
                              最晚複習時間：{formatTaipeiDateTime(m.last_review_time)}
                            </div>
                          )}
                        </div>
                      </div>
                    )}
                  </div>
                ))}

                {/* Mistakes Load More */}
                {mistakes.length < totalMistakes && (
                  <button
	                    id="btn-load-more-mistakes"
	                    onClick={() => {
	                      loadMistakes(false, mistakesPage + 1);
	                    }}
                    className="mistakes-load-more"
                  >
                    {isLoadingMistakes ? '載入中…' : '載入更多'}
                  </button>
                )}
              </div>
            )}
          </div>
        )}

        {/* Panel 2: Confusions */}
        {activeTab === 'confusions' && (
          <div id="tab-panel-confusions" role="tabpanel" aria-labelledby="tab-btn-confusions" className="mistakes-tab-panel">
            
            {/* Sorting Switcher */}
            <div className="mistakes-sort-bar">
              <span className="mistakes-sort-count">
                共有 {totalConfusions} 對高頻混淆組合
              </span>
              
              <div role="group" aria-label="混淆排序方式" className="mistakes-sort-options">
                {(['count', 'activity'] as const).map((o) => (
                  <button
                    key={o}
                    id={`btn-order-confusion-${o}`}
                    type="button"
                    aria-pressed={orderBy === o}
                    onClick={() => setOrderBy(o)}
                    className="mistakes-sort-option"
                  >
                    {o === 'count' ? '次數排序' : '時間排序'}
                  </button>
                ))}
              </div>
            </div>

            {/* Confusions List */}
            {confusions.length === 0 ? (
              <div className="empty-state mistakes-empty-state">
                <MaterialSymbol name="check_circle" fill className="empty-state-icon" />
                <div className="mistakes-empty-title">尚無混淆數據</div>
                <div className="mistakes-empty-copy">若在學習時選錯中文干擾選項，混淆組合會出現在這裡。</div>
              </div>
            ) : (
              <div className="mistakes-list">
                {confusions.map((c, idx) => (
                  <div key={idx} className="card content-auto mistakes-entry">
                    <button
                      id={`btn-confusion-pair-${idx}`}
                      onClick={() => setExpandedConfusionIdx(expandedConfusionIdx === idx ? null : idx)}
                      className="mistakes-entry-trigger"
                    >
                      <div className="mistakes-entry-main">
                        <div className="confusion-pair">
                          <span className="confusion-pair-target">
                            {c.target_card.english}
                          </span>
                          <MaterialSymbol name="chevron_right" className="inline-separator-icon" />
                          <span className="confusion-pair-wrong">
                            {c.confused_card.english}
                          </span>
                        </div>
                        <div className="long-text confusion-pair-description">
                          將「{c.target_card.chinese_meaning}」錯誤認作「{c.confused_card.chinese_meaning}」
                        </div>
                      </div>
                      <div className="mistakes-entry-count">
                        <span className="stat-pill stat-pill-again confusion-count-pill">
                          混淆 {c.occurrence_count} 次
                        </span>
                      </div>
                    </button>

                    {expandedConfusionIdx === idx && (
                      <div className="mistakes-entry-details">
                        <div className="confusion-details">
                          
                          {/* Target card specs */}
                          <div className="confusion-card">
                            <div className="confusion-card-title">
                              正確單字: {c.target_card.english}
                            </div>
                            <div className="confusion-card-meaning">
                              {c.target_card.part_of_speech ? `[${c.target_card.part_of_speech}] ` : ''}{c.target_card.chinese_meaning}
                            </div>
                            {c.target_card.example_sentence && (
                              <div className="confusion-example confusion-example-target">
                                <div className="confusion-example-sentence">
                                  {c.target_card.example_sentence}
                                </div>
                                {c.target_card.example_translation && (
                                  <div className="confusion-example-translation">
                                    {c.target_card.example_translation}
                                  </div>
                                )}
                              </div>
                            )}
                          </div>

                          {/* Confused card specs */}
                          <div className="confusion-card">
                            <div className="confusion-card-title">
                              被認錯成的單字: {c.confused_card.english}
                            </div>
                            <div className="confusion-card-meaning">
                              {c.confused_card.part_of_speech ? `[${c.confused_card.part_of_speech}] ` : ''}{c.confused_card.chinese_meaning}
                            </div>
                            {c.confused_card.example_sentence && (
                              <div className="confusion-example confusion-example-wrong">
                                <div className="confusion-example-sentence">
                                  {c.confused_card.example_sentence}
                                </div>
                                {c.confused_card.example_translation && (
                                  <div className="confusion-example-translation">
                                    {c.confused_card.example_translation}
                                  </div>
                                )}
                              </div>
                            )}
                          </div>

                          {c.last_occurred_at && (
                            <div className="mistakes-timestamp">
                              最近一次混淆時間：{formatTaipeiDateTime(c.last_occurred_at)}
                            </div>
                          )}
                        </div>
                      </div>
                    )}
                  </div>
                ))}

                {/* Confusions Load More */}
                {confusions.length < totalConfusions && (
                  <button
	                    id="btn-load-more-confusions"
	                    onClick={() => {
	                      loadConfusions(false, confusionsPage + 1);
	                    }}
                    className="mistakes-load-more"
                  >
                    {isLoadingConfusions ? '載入中…' : '載入更多'}
                  </button>
                )}
              </div>
            )}
          </div>
        )}

      </div>

      {showExportModal && (
        <MistakesExportDialog
          dialogRef={exportDialogRef}
          closeRef={exportCloseRef}
          filterType={exportFilterType}
          setFilterType={setExportFilterType}
          limitType={exportLimitType}
          setLimitType={setExportLimitType}
          customLimit={exportCustomLimit}
          setCustomLimit={setExportCustomLimit}
          preview={exportPreview}
          isExporting={isExporting}
          copyFeedback={copyFeedback}
          onClose={closeExportModal}
          onCopy={handleCopyToClipboard}
          onDownload={handleDownloadFile}
        />
      )}

    </div>
  );
}
